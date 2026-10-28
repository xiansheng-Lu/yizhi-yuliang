"""真实PG与会话HTTP；所有主体/授权/字段证据均合成，不调用平台。"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.models import Binding
from yuliang.modules.capability.service import register_binding
from yuliang.modules.catalog.service import link_authorization
from yuliang.modules.configuration.evidence import register_field_proof
from yuliang.modules.configuration.models import ConfigurationContext, FieldProof
from yuliang.modules.configuration.service import ConfigurationService
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_capability_evidence import populate
from .test_identity_database import database as database
from .test_oauth_vault import SyntheticExchange
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api

pytestmark = pytest.mark.integration
PREFIX = "/v1/delivery/configuration"


@pytest.fixture
async def configuration_ready(
    capability: dict[str, Any], oauth_ready: dict[str, Any]
) -> dict[str, Any]:
    data, oauth = capability, oauth_ready
    start = oauth["service"].start(
        oauth["identity"], oauth["session"], oauth["application"], uuid4()
    )
    await oauth["service"].complete(
        start.state, SecretStr("synthetic-code"), oauth["application"], SyntheticExchange(oauth)
    )
    entity = uuid4()
    binding: Binding = data["binding"].model_copy(
        update={
            "id": uuid4(),
            "operation_kind": "READ",
            "http_method": "GET",
            "normalized_path": "/v3.0/adgroups/get",
            "catalog_id": "90b086afd649f5dc0600",
            "permission_code": "overview.read",
            "action": "analyze",
        }
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO business_entities(id,tenant_id,name,kind) "
                "VALUES(:id,:tenant,'Synthetic entity','ADVERTISER')"
            ),
            {"id": entity, "tenant": data["tenant"]},
        )
        owner.execute(
            text("UPDATE ad_accounts SET entity_id=:entity WHERE id=:id"),
            {"entity": entity, "id": binding.account_id},
        )
        owner.execute(
            text("UPDATE shops SET entity_id=:entity WHERE id=:id"),
            {"entity": entity, "id": data["shop"]},
        )
        link_authorization(
            owner, start.authorization_id, binding.account_id, "synthetic/M3-07/link"
        )
        register_binding(owner, binding)
    populate(data, execution=True, target=binding)
    context = ConfigurationContext.model_validate(
        dict(
            product="adq",
            scenario="ADQ",
            business_entity_id=entity,
            account_id=binding.account_id,
            shop_id=str(data["shop"]),
            objective_id="objective-A",
            optimization_id="",
            strategy_id="",
            target_ids=("target-A",),
            placement_ids=("placement-A",),
            bid_mode_id="",
            order_type_id="",
            fund_type_id="",
            delivery_mode_id="",
            creative_method_id="",
        )
    )
    service = ConfigurationService(
        data["engine"], data["identity"], data["settings"].auth_audit_secret
    )
    return data | {
        "configuration": service,
        "context": context,
        "read_binding": binding,
        "authorization": start.authorization_id,
    }


def query(context: ConfigurationContext) -> dict[str, Any]:
    values = context.model_dump(mode="json", by_alias=True)
    return values | {
        "targetIds": ",".join(context.target_ids),
        "placementIds": ",".join(context.placement_ids),
    }


def proof(
    data: dict[str, Any],
    *,
    field: str = "budget",
    capability: dict[str, Any] | None = None,
    context: ConfigurationContext | None = None,
    expired: bool = False,
) -> FieldProof:
    now = datetime.now(UTC)
    return FieldProof.model_validate(
        dict(
            context=context or data["context"],
            field_id=field,
            capability=capability
            or dict(
                state="AVAILABLE",
                value_type="INTEGER",
                reason="合成字段核对依据",
                unit="CNY_MINOR",
                min=0,
                max=100000,
                step=1,
                precision=0,
            ),
            binding_id=data["read_binding"].id,
            binding_version=data["read_binding"].revision,
            authorization_id=data["authorization"],
            authorization_version=1,
            required_scopes=("read",),
            proof_ref="synthetic/M3-07/field",
            checked_at=now - timedelta(minutes=2),
            expires_at=now - timedelta(seconds=1) if expired else now + timedelta(minutes=10),
        )
    )


def record(data: dict[str, Any], value: FieldProof) -> None:
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert register_field_proof(owner, data["tenant"], value) == value.id


def test_actual_catalog_http_and_full_frontend_contract(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    record(data, proof(data))
    client = data["client"]
    catalog = client.get(PREFIX + "/catalog", params={"product": "adq"})
    capability = client.get(PREFIX + "/capabilities", params=query(data["context"]))
    assert catalog.status_code == capability.status_code == 200, capability.text
    directory, value = catalog.json()["data"], capability.json()["data"]
    assert directory["accounts"][0]["id"] == str(data["context"].account_id)
    assert directory["accounts"][0]["shopIds"] == [str(data["shop"])]
    assert directory["accounts"][0]["authorization"] == "AUTHORIZED"
    assert directory["accounts"][0]["writable"] is False
    assert value["contextKey"] == data["context"].context_key
    assert data["configuration"].capabilities(data["context"]).version == value["version"]
    assert value["fields"]["budget"]["state"] == "AVAILABLE"
    assert (
        value["fields"]["budget"]["unit"] == "CNY_MINOR" and value["fields"]["budget"]["min"] == 0
    )
    assert value["writeChannel"] == "NONE" and value["authorization"] == "NOT_INTEGRATED"
    assert not value["estimateSupported"] and not value["uploadSupported"]
    assert value["verification"]["executionVerified"] is False
    assert "private-access" not in capability.text and "native_id" not in catalog.text
    assert capability.headers["cache-control"] == "no-store"
    if target := os.getenv("YULIANG_CONFIGURATION_EVIDENCE"):
        Path(target).write_text(
            json.dumps(
                {
                    "catalog": directory,
                    "capability": value,
                    "context": data["context"].model_dump(mode="json", by_alias=True),
                    "identity": client.get("/v1/auth/session").json()["data"],
                    "input": "SYNTHETIC_IDENTITIES_AUTHORIZATION_FIELD_PROOFS_REAL_PG_HTTP",
                    "platformCalls": 0,
                    "paidCalls": 0,
                    "humanSignoff": "PENDING",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


@pytest.mark.parametrize(
    "dimension",
    [
        "target_ids",
        "placement_ids",
        "objective_id",
        "strategy_id",
        "bid_mode_id",
        "fund_type_id",
        "delivery_mode_id",
        "creative_method_id",
    ],
)
def test_field_proofs_are_not_reused_across_contexts(
    configuration_ready: dict[str, Any], dimension: str
) -> None:
    data = configuration_ready
    record(data, proof(data))
    other = data["context"].model_copy(
        update={dimension: ("other",) if dimension.endswith("_ids") else "other"}
    )
    view = data["configuration"].capabilities(other)
    assert (
        view.context_key != data["context"].context_key
        and view.fields["budget"].state == "UNCONFIRMED"
    )
    record(
        data,
        proof(
            data,
            context=other,
            capability=dict(
                state="NOT_APPLICABLE", value_type="INTEGER", reason="合成上下文不适用"
            ),
        ),
    )
    assert data["configuration"].capabilities(other).fields["budget"].state == "NOT_APPLICABLE"
    assert data["configuration"].capabilities(data["context"]).fields["budget"].state == "AVAILABLE"


@pytest.mark.parametrize(
    "change,expected",
    [
        ("expired_field", "UNCONFIRMED"),
        ("expired_capability", "API_PENDING"),
        ("revoked", "AUTH_REQUIRED"),
        ("grant_version", "AUTH_REQUIRED"),
        ("scope", "AUTH_REQUIRED"),
        ("binding", "UNCONFIRMED"),
    ],
)
def test_current_sources_downgrade_instead_of_reusing_stale_evidence(
    configuration_ready: dict[str, Any], change: str, expected: str
) -> None:
    data = configuration_ready
    record(data, proof(data, expired=change == "expired_field"))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        commands = {
            "expired_capability": "UPDATE capability_assertions "
            "SET expires_at=clock_timestamp()-interval '1 second' WHERE binding_id=:binding",
            "revoked": "UPDATE provider_authorizations SET status='REVOKED' WHERE id=:auth",
            "grant_version": "UPDATE provider_authorizations SET version=version+1 WHERE id=:auth",
            "scope": "UPDATE provider_authorizations SET scopes=ARRAY['other'] WHERE id=:auth",
            "binding": "UPDATE capability_bindings SET active=false WHERE id=:binding",
        }
        if change in commands:
            owner.execute(
                text(commands[change]),
                {"auth": data["authorization"], "binding": data["read_binding"].id},
            )
    assert data["configuration"].capabilities(data["context"]).fields["budget"].state == expected


def test_append_only_revision_replay_rollback_and_runtime_cannot_forge(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    first = proof(data)
    record(data, first)
    record(data, first)
    with pytest.raises(ValueError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_field_proof(
            owner, data["tenant"], first.model_copy(update={"proof_ref": "synthetic/changed"})
        )
    with pytest.raises(RuntimeError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_field_proof(owner, data["tenant"], proof(data, expired=True))
        raise RuntimeError("synthetic rollback")
    assert data["configuration"].capabilities(data["context"]).fields["budget"].state == "AVAILABLE"
    record(data, proof(data, expired=True))
    assert (
        data["configuration"].capabilities(data["context"]).fields["budget"].state == "UNCONFIRMED"
    )
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM configuration_field_assertions")
            ).scalar_one()
            == 2
        )
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(text("UPDATE configuration_field_assertions SET revision=100"))
    with (
        pytest.raises(ValueError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        register_field_proof(session.connection(), data["tenant"], first)


def test_both_shop_and_account_scope_required_and_tenant_isolation(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    record(data, proof(data))
    alien = ConfigurationService(
        data["engine"],
        VerifiedIdentity(data["tenant"] + 1, data["member"], data["user"]),
        data["settings"].auth_audit_secret,
    )
    assert alien.catalog("adq").accounts == ()
    with business_transaction(data["engine"], alien.identity) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM configuration_field_assertions")
            ).scalar_one()
            == 0
        )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:member"), {"member": data["member"]}
        )
        owner.execute(
            text(
                "INSERT INTO data_scopes(tenant_id,membership_id,scope_type,account_id) "
                "VALUES(:tenant,:member,'ACCOUNT',:account)"
            ),
            {
                "tenant": data["tenant"],
                "member": data["member"],
                "account": data["context"].account_id,
            },
        )
    assert data["configuration"].catalog("adq").accounts == ()
    with pytest.raises(ApiError):
        data["configuration"].capabilities(data["context"])
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO data_scopes(tenant_id,membership_id,scope_type,shop_id) "
                "VALUES(:tenant,:member,'SHOP',:shop)"
            ),
            {"tenant": data["tenant"], "member": data["member"], "shop": data["shop"]},
        )
    assert len(data["configuration"].catalog("adq").accounts) == 1


def test_unmapped_or_unsupported_products_return_actual_empty_catalog(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    for product in (
        "ocean_marketing",
        "dou_plus",
        "local_push",
        "shop",
        "qianchuan",
        "beans",
        "juguang",
    ):
        response = data["client"].get(PREFIX + "/catalog", params={"product": product})
        assert response.status_code == 200 and response.json()["data"]["accounts"] == []
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE shops SET entity_id=NULL WHERE id=:shop"), {"shop": data["shop"]}
        )
    assert data["configuration"].catalog("adq").accounts == ()


def test_http_cookie_origin_unknown_and_repeated_context_rejected(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    client, values = data["client"], query(data["context"])
    assert (
        client.get(
            PREFIX + "/catalog?product=adq", headers={"Origin": "https://other.invalid"}
        ).status_code
        == 403
    )
    for mutation in (
        values | {"tenantId": "999"},
        values | {"authorizationId": str(data["authorization"])},
        values | {"targetIds": "a,a"},
        {k: v for k, v in values.items() if k != "objectiveId"},
    ):
        assert client.get(PREFIX + "/capabilities", params=mutation).status_code == 422
    assert (
        client.get(
            PREFIX + "/capabilities", params=list(values.items()) + [("targetIds", "another")]
        ).status_code
        == 422
    )
    client.cookies.clear()
    assert client.get(PREFIX + "/capabilities", params=values).status_code == 401


def test_scoped_options_and_all_field_states_preserve_actual_evidence(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    cases: dict[str, dict[str, Any]] = {
        "objective": dict(
            state="AVAILABLE",
            value_type="SELECT",
            reason="synthetic",
            options=[
                dict(id="actual-option-A", name="Synthetic permitted", available=True),
                dict(
                    id="actual-option-B",
                    name="Synthetic disabled",
                    available=False,
                    reason="not in current scope",
                ),
            ],
        ),
        "region": dict(
            state="API_PENDING", value_type="MULTI_SELECT", reason="synthetic missing interface"
        ),
        "placement": dict(
            state="AUTH_REQUIRED", value_type="MULTI_SELECT", reason="synthetic missing grant"
        ),
        "bid": dict(
            state="NOT_APPLICABLE", value_type="DECIMAL", reason="synthetic context not applicable"
        ),
    }
    for field, field_capability in cases.items():
        record(data, proof(data, field=field, capability=field_capability))
    view = data["configuration"].capabilities(data["context"])
    assert {value.state for value in view.fields.values()} == {
        "PUBLIC",
        "AVAILABLE",
        "API_PENDING",
        "AUTH_REQUIRED",
        "NOT_APPLICABLE",
        "UNCONFIRMED",
    }
    assert view.fields["objective"].options is not None
    assert [o.id for o in view.fields["objective"].options] == [
        "actual-option-A",
        "actual-option-B",
    ]
    assert view.fields["objective"].options[1].available is False
    old_version = view.version
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE data_scopes SET permissions_mask=ARRAY['snapshot.read','overview.read'] "
                "WHERE membership_id=:member"
            ),
            {"member": data["member"]},
        )
    changed = data["configuration"].capabilities(data["context"])
    assert changed.version != old_version and changed.authorization == "FORBIDDEN"
    assert changed.write_channel == "NONE"


def test_maintenance_rejects_cross_account_grant_and_mismatched_source(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    source = proof(data)
    for update in (
        {"authorization_version": 999},
        {"required_scopes": ("ungranted",)},
        {"binding_version": 999},
        {"authorization_id": uuid4()},
    ):
        with pytest.raises(ValueError), data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            register_field_proof(owner, data["tenant"], source.model_copy(update=update))
    # 能力上下文在数据库层不可覆盖；替换能力必须创建新绑定并重做字段依据。
    with pytest.raises(DBAPIError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE capability_bindings SET revision=revision+1 WHERE id=:id"),
            {"id": data["read_binding"].id},
        )


def test_empty_optional_shop_uses_visible_pair_without_reusing_specific_shop_proof(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    record(data, proof(data))
    context = data["context"].model_copy(update={"shop_id": ""})
    view = data["configuration"].capabilities(context)
    assert view.context_key == context.context_key and view.fields["budget"].state == "UNCONFIRMED"
