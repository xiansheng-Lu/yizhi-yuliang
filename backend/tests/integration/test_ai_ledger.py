"""真实PG验证模型/提示词版本、主备调用事实、未知费用与跨租户边界；无供应商请求。"""

import json
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.aikernel.ledger import InvocationLedger, UnavailableGateway
from yuliang.modules.aikernel.models import (
    Completion,
    InvocationRequest,
    ModelGateway,
    ModelSpec,
    Usage,
)
from yuliang.platform.db import VerifiedIdentity, business_transaction

from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api


@pytest.fixture
def ai_data(login_api: dict[str, Any]) -> dict[str, Any]:
    spec = ModelSpec(
        model_key="example.primary",
        provider="test",
        model="example-v1",
        endpoint_ref="PRIMARY_ENDPOINT",
        secret_ref="PRIMARY_KEY",
        billing_currency="CNY",
    )
    backup = spec.model_copy(
        update={
            "model_key": "example.backup",
            "model": "example-v2",
            "endpoint_ref": "BACKUP_ENDPOINT",
            "secret_ref": "BACKUP_KEY",
        }
    )
    config_id, prompt_id = uuid4(), uuid4()
    with psycopg.connect(login_api["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "INSERT INTO model_configs(id,tenant_id,capability_alias,revision,"
            "primary_spec,backup_spec) "
            "VALUES(%s,%s,'copywriting.default',1,%s::jsonb,%s::jsonb)",
            [config_id, login_api["tenant"], spec.model_dump_json(), backup.model_dump_json()],
        )
        control.execute(
            "INSERT INTO prompt_versions(id,tenant_id,prompt_key,version,capability_alias,template,"
            "content_hash,output_schema_ref,status,created_by) "
            "VALUES(%s,%s,'copywriting',1,'copywriting.default','Only create a review candidate.',"
            "encode(sha256(convert_to('Only create a review candidate.','UTF8')),'hex'),"
            "'copywriting-v1','ACTIVE',%s)",
            [prompt_id, login_api["tenant"], login_api["user"]],
        )
    identity = VerifiedIdentity(login_api["tenant"], login_api["member"], login_api["user"])
    ledger = InvocationLedger(login_api["app"].state.identity_service.app, identity)
    request = InvocationRequest(
        call_id=uuid4(),
        request_id=uuid4(),
        scenario="copywriting",
        capability_alias="copywriting.default",
        prompt_key="copywriting",
        prompt_version=1,
    )
    return {
        "api": login_api,
        "ledger": ledger,
        "request": request,
        "config": config_id,
        "prompt": prompt_id,
        "primary": spec,
        "backup": backup,
    }


def stored(data: dict[str, Any], request: InvocationRequest | None = None) -> dict[str, Any]:
    ledger: InvocationLedger = data["ledger"]
    with business_transaction(ledger.engine, ledger.identity) as session:
        return dict(
            session.execute(
                text("SELECT * FROM ai_invocations WHERE call_id=:id"),
                {"id": (request or data["request"]).call_id},
            )
            .mappings()
            .one()
        )


def test_m2_01a_call_is_committed_before_any_provider_result(ai_data: dict[str, Any]) -> None:
    snapshot = ai_data["ledger"].begin(ai_data["request"])
    row = stored(ai_data)
    assert (
        row["status"] == "STARTED"
        and row["dispatch_state"] == "NOT_SENT"
        and row["finished_at"] is None
    )
    assert row["tokens_in"] is None and row["tokens_out"] is None and row["cost_minor"] is None
    assert row["request_id"] == ai_data["request"].request_id
    assert snapshot["model_snapshot"]["primary"] == ai_data["primary"].model_dump()
    assert snapshot["model_snapshot"]["backup"] == ai_data["backup"].model_dump()
    assert len(snapshot["prompt_hash"]) == 64
    assert "api_key" not in json.dumps(row["model_config_snapshot"])
    with pytest.raises(DBAPIError):
        ai_data["ledger"].begin(ai_data["request"])


@pytest.mark.parametrize("status", ["FAILED", "TIMEOUT", "RATE_LIMITED", "FALLBACK"])
def test_m2_01a_failure_never_assumes_zero_cost(ai_data: dict[str, Any], status: str) -> None:
    ledger = ai_data["ledger"]
    ledger.begin(ai_data["request"])
    ledger.finish(
        ai_data["request"], Completion.model_validate({"status": status, "latency_ms": 12})
    )
    row = stored(ai_data)
    assert row["status"] == status and row["usage_state"] == row["cost_state"] == "UNKNOWN"
    assert row["tokens_in"] is None and row["tokens_out"] is None and row["cost_minor"] is None
    assert row["finished_at"] is not None


def test_m2_01a_reported_usage_is_explicit_and_cannot_be_overwritten(
    ai_data: dict[str, Any],
) -> None:
    ledger = ai_data["ledger"]
    ledger.begin(ai_data["request"])
    ledger.finish(
        ai_data["request"],
        Completion(
            status="OK",
            dispatch_state="RESPONDED",
            latency_ms=17,
            usage=Usage(
                usage_state="KNOWN",
                tokens_in=100,
                tokens_out=20,
                cost_state="KNOWN",
                cost_minor=3,
                cost_currency="CNY",
            ),
        ),
    )
    with pytest.raises(ValueError, match="不能覆盖"):
        ledger.finish(ai_data["request"], Completion(status="TIMEOUT", latency_ms=19))
    row = stored(ai_data)
    assert (row["status"], row["tokens_in"], row["tokens_out"], row["cost_minor"]) == (
        "OK",
        100,
        20,
        3,
    )


def test_m2_01a_backup_uses_original_route_and_parent_trace(ai_data: dict[str, Any]) -> None:
    ledger = ai_data["ledger"]
    ledger.begin(ai_data["request"])
    ledger.finish(ai_data["request"], Completion(status="TIMEOUT", latency_ms=20))
    with psycopg.connect(ai_data["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "INSERT INTO model_configs(tenant_id,capability_alias,revision,primary_spec) "
            "VALUES(%s,'copywriting.default',2,%s::jsonb)",
            [ledger.identity.tenant_id, ai_data["primary"].model_dump_json()],
        )
    backup = ai_data["request"].model_copy(
        update={
            "call_id": uuid4(),
            "model_role": "BACKUP",
            "parent_call_id": ai_data["request"].call_id,
        }
    )
    snapshot = ledger.begin(backup)
    assert snapshot["model_snapshot"]["revision"] == 1
    row = stored(ai_data, backup)
    assert row["model_key"] == ai_data["backup"].model_key
    assert (
        row["parent_call_id"] == ai_data["request"].call_id
        and row["model_config_id"] == ai_data["config"]
    )


def test_m2_01a_gateway_unavailable_is_explicit_and_not_a_model_result(
    ai_data: dict[str, Any],
) -> None:
    gateway: ModelGateway = UnavailableGateway(ai_data["ledger"])
    candidate = gateway.generate(ai_data["request"])
    assert (
        candidate.source == "RULES"
        and candidate.status == "UNAVAILABLE"
        and candidate.requires_review
    )
    row = stored(ai_data)
    assert row["status"] == "FALLBACK" and row["dispatch_state"] == "NOT_SENT"
    assert row["cost_minor"] is None and row["tokens_in"] is None


def test_m2_01a_rls_and_member_scope_deny_cross_tenant(ai_data: dict[str, Any]) -> None:
    ledger = ai_data["ledger"]
    ledger.begin(ai_data["request"])
    member = uuid4()
    with psycopg.connect(ai_data["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        tenant = control.execute(
            "INSERT INTO tenants(name) VALUES('Other AI tenant') RETURNING id"
        ).fetchone()
        assert tenant
        control.execute(
            "INSERT INTO memberships(id,tenant_id,user_id) VALUES(%s,%s,%s)",
            [member, tenant[0], ledger.identity.user_id],
        )
    other = VerifiedIdentity(tenant[0], member, ledger.identity.user_id)
    with business_transaction(ledger.engine, other) as session:
        assert session.execute(text("SELECT count(*) FROM ai_invocations")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM model_configs")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM prompt_versions")).scalar_one() == 0
    with pytest.raises(ValueError):
        InvocationLedger(ledger.engine, other).finish(
            ai_data["request"], Completion(status="TIMEOUT", latency_ms=1)
        )
    forged = VerifiedIdentity(tenant[0], ledger.identity.membership_id, ledger.identity.user_id)
    with pytest.raises(DBAPIError):
        InvocationLedger(ledger.engine, forged).begin(
            ai_data["request"].model_copy(update={"call_id": uuid4()})
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ai_invocations SET cost_minor=0",
        "DELETE FROM ai_invocations",
        "UPDATE model_configs SET revision=revision+1",
        "DELETE FROM prompt_versions",
    ],
)
def test_m2_01a_runtime_cannot_rewrite_configuration_or_cost(
    ai_data: dict[str, Any], statement: str
) -> None:
    ledger = ai_data["ledger"]
    with pytest.raises(DBAPIError), business_transaction(ledger.engine, ledger.identity) as session:
        session.execute(text(statement))


def test_m2_01a_database_also_rejects_unknown_zero(ai_data: dict[str, Any]) -> None:
    ledger = ai_data["ledger"]
    ledger.begin(ai_data["request"])
    with pytest.raises(DBAPIError), business_transaction(ledger.engine, ledger.identity) as session:
        session.execute(
            text(
                "SELECT access.ai_finish(:call,:user,'TIMEOUT','UNKNOWN','UNKNOWN',"
                "NULL,NULL,0,1,NULL,'UNKNOWN',NULL)"
            ),
            {"call": ai_data["request"].call_id, "user": ledger.identity.user_id},
        )
    assert stored(ai_data)["status"] == "STARTED" and stored(ai_data)["cost_minor"] is None


def test_m2_01a_configuration_rejects_raw_credentials(ai_data: dict[str, Any]) -> None:
    spec = ai_data["primary"].model_dump() | {"api_key": "NeverStorePrivateCredential"}
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        psycopg.connect(ai_data["api"]["database"]["migration"]) as control,
    ):
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "INSERT INTO model_configs(tenant_id,capability_alias,revision,primary_spec) "
            "VALUES(%s,'copywriting.default',2,%s::jsonb)",
            [ai_data["ledger"].identity.tenant_id, json.dumps(spec)],
        )


def test_m2_01a_cost_currency_must_match_selected_model(ai_data: dict[str, Any]) -> None:
    ledger = ai_data["ledger"]
    ledger.begin(ai_data["request"])
    with pytest.raises(DBAPIError):
        ledger.finish(
            ai_data["request"],
            Completion(
                status="OK",
                dispatch_state="RESPONDED",
                latency_ms=1,
                usage=Usage(cost_state="KNOWN", cost_minor=3, cost_currency="USD"),
            ),
        )
    assert stored(ai_data)["status"] == "STARTED" and stored(ai_data)["cost_minor"] is None


def test_m2_01a_backup_cannot_start_while_primary_is_inflight(ai_data: dict[str, Any]) -> None:
    ledger = ai_data["ledger"]
    ledger.begin(ai_data["request"])
    backup = ai_data["request"].model_copy(
        update={
            "call_id": uuid4(),
            "model_role": "BACKUP",
            "parent_call_id": ai_data["request"].call_id,
        }
    )
    with pytest.raises(DBAPIError):
        ledger.begin(backup)


def test_m2_01a_disabled_prompt_cannot_start_call(ai_data: dict[str, Any]) -> None:
    with psycopg.connect(ai_data["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "UPDATE prompt_versions SET status='DISABLED' WHERE id=%s", [ai_data["prompt"]]
        )
    with pytest.raises(DBAPIError):
        ai_data["ledger"].begin(ai_data["request"])
