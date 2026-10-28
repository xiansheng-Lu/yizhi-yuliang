"""真实PG/Redis网关的固定SDK路由依据；所有令牌/HTTP/阶段结果为合成输入。"""

from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.models import Evidence, Phase
from yuliang.modules.capability.route_sources import PROFILES, ReadRouteSource
from yuliang.modules.capability.sdk_catalog import import_sdk_read_catalog
from yuliang.modules.capability.service import record_evidence, register_binding
from yuliang.modules.catalog.service import link_authorization
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.governance import PlatformGovernance
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.native_reads import decode_verdict, native_signer, read_spec
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_capability_evidence import capability as capability
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import setup

pytestmark = pytest.mark.integration


@pytest.fixture
def legacy(configuration_ready: dict[str, Any]) -> dict[str, Any]:
    data = configuration_ready
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        catalog = import_sdk_read_catalog(owner, "ADQ_CAMPAIGNS_V1_3_READ")
    binding = data["read_binding"].model_copy(
        update={
            "id": uuid4(),
            "catalog_id": catalog["catalogId"],
            "catalog_sha256": catalog["catalogSha256"],
            "normalized_path": "/v1.3/campaigns/get",
            "action": "analyze",
        }
    )
    source = ReadRouteSource(
        profile="ADQ_CAMPAIGNS_V1_3_READ",
        source_sha256=PROFILES["ADQ_CAMPAIGNS_V1_3_READ"].source_sha256,
        checked_by="SYNTHETIC_TEST_SOURCE_REFERENCE",
        checked_at=data["now"] - timedelta(minutes=1),
        expires_at=data["now"] + timedelta(hours=1),
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE ad_accounts SET native_id='12345' WHERE id=:id"),
            {"id": binding.account_id},
        )
        link_authorization(
            owner, data["authorization"], binding.account_id, "synthetic/M1-08/native"
        )
        register_binding(owner, binding, route_source=source)
        kinds = (
            "DOCUMENTATION",
            "SCOPE_GRANT",
            "ACCOUNT_AUTH",
            "ADAPTER_INTEGRATION",
            "READ_SCHEMA",
            "READ_RESULT",
        )
        for index, phase in enumerate(Phase):
            record_evidence(
                owner,
                binding.id,
                Evidence.model_validate(
                    dict(
                        id=uuid4(),
                        binding_id=binding.id,
                        phase=phase,
                        result="PASS",
                        proof_kind=kinds[index],
                        proof_ref=source.proof_ref
                        if phase == Phase.PATH
                        else "synthetic/M1-08/phase",
                        checked_by="SYNTHETIC_TEST",
                        checked_at=data["now"] + timedelta(microseconds=index),
                        expires_at=source.expires_at,
                    )
                ),
            )
    context, _, _, archive = setup(data)
    context = replace(
        context,
        authorization_id=data["authorization"],
        binding_id=binding.id,
        catalog_id=binding.catalog_id,
        catalog_sha256=binding.catalog_sha256,
        native_account_id="12345",
    )
    return data | {
        "route_binding": binding,
        "route_source": source,
        "ctx": context,
        "archive": archive,
        "readspec": read_spec("adq.campaigns", binding.catalog_id, binding.catalog_sha256),
    }


def test_catalog_is_unchanged_and_binding_requires_explicit_readonly_sdk_source(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        catalog = import_sdk_read_catalog(owner, "ADQ_CAMPAIGNS_V1_3_READ")
        assert import_sdk_read_catalog(owner, "ADQ_CAMPAIGNS_V1_3_READ") == catalog
    binding = data["read_binding"].model_copy(
        update={
            "id": uuid4(),
            "catalog_id": catalog["catalogId"],
            "catalog_sha256": catalog["catalogSha256"],
            "normalized_path": "/v1.3/campaigns/get",
        }
    )
    with pytest.raises(ValueError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_binding(owner, binding)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT normalized_path FROM endpoint_catalog WHERE catalog_id=:id"),
                {"id": catalog["catalogId"]},
            ).scalar_one()
            == "/v1.3/campaigns/get"
        )
        assert (
            session.execute(text("SELECT catalog_sha256 FROM catalog_head")).scalar_one()
            == data["bundle"].sha256
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM endpoint_catalog WHERE catalog_sha256=:sha"),
                {"sha": data["bundle"].sha256},
            ).scalar_one()
            == 1818
        )
        for kind, expected in (("READ", True), ("WRITE", False)):
            assert (
                session.execute(
                    text("SELECT access.catalog_is_current(:sha,:kind)"),
                    {"sha": catalog["catalogSha256"], "kind": kind},
                ).scalar_one()
                is expected
            )


@pytest.mark.parametrize("expiry_mode", ["COLUMNS_ONLY", "PAYLOAD_ONLY", "BOTH"])
async def test_actual_outbound_uses_sdk_path_and_expired_source_stops_before_http(
    legacy: dict[str, Any], governance: PlatformGovernance, expiry_mode: str
) -> None:
    data = legacy
    calls: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert (
            request.url.path == "/v1.3/campaigns/get"
            and request.url.params["account_id"] == "12345"
        )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "list": [],
                    "page_info": {"page": 1, "page_size": 100, "total_number": 0, "total_page": 0},
                },
            },
        )

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            data["archive"],
            decoder=decode_verdict,
        ),
        governance,
        uuid4(),
    )
    params = dict(account_id="12345", fields=["campaign_id"], page=1, page_size=100)
    result = await client.send(
        data["ctx"], data["readspec"], uuid4(), params, SecretStr("synthetic-token"), lane="SYNC"
    )
    assert len(calls) == 1 and result.metadata["externalOutcome"] == "READ_SUCCEEDED"
    expired = data["route_source"].model_copy(
        update={"expires_at": data["now"] - timedelta(seconds=1)}
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE capability_route_sources SET expires_at=:expires,"
                "payload=CAST(:payload AS jsonb) WHERE id=:id"
            ),
            {
                "id": expired.id,
                "expires": data["route_source"].expires_at
                if expiry_mode == "PAYLOAD_ONLY"
                else expired.expires_at,
                "payload": data["route_source"].model_dump_json()
                if expiry_mode == "COLUMNS_ONLY"
                else expired.model_dump_json(),
            },
        )
    with pytest.raises(ApiError) as stopped:
        await client.send(
            data["ctx"],
            data["readspec"],
            uuid4(),
            params,
            SecretStr("synthetic-token"),
            lane="SYNC",
        )
    assert stopped.value.code == ErrorCode.CAPABILITY_UNAVAILABLE and len(calls) == 1


def test_route_source_does_not_allow_write_or_unrelated_catalog_and_path_phase_cannot_outlive_it(
    legacy: dict[str, Any],
) -> None:
    data = legacy
    for update in (
        {"normalized_path": "/v1.3/campaigns/delete", "operation_kind": "WRITE"},
        {"catalog_id": data["read_binding"].catalog_id},
    ):
        with pytest.raises(ValueError), data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            register_binding(
                owner,
                data["route_binding"].model_copy(update={"id": uuid4()} | update),
                route_source=data["route_source"].model_copy(update={"id": uuid4()}),
            )
    with pytest.raises(ValueError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        record_evidence(
            owner,
            data["route_binding"].id,
            Evidence(
                id=uuid4(),
                binding_id=data["route_binding"].id,
                phase=Phase.PATH,
                result="PASS",
                proof_kind="DOCUMENTATION",
                proof_ref=data["route_source"].proof_ref,
                checked_by="SYNTHETIC_TEST",
                checked_at=data["now"],
                expires_at=data["now"] + timedelta(hours=2),
            ),
        )
    with (
        pytest.raises(DBAPIError) as denied,
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(text("DELETE FROM capability_route_sources"))
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
