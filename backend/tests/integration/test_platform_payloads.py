"""真实PG保存/隔离/故障边界；平台输出来自合成MockTransport，不计真实平台验收。"""

import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.service import register_binding
from yuliang.modules.connectors.archive import (
    ArchiveUnavailable,
    PlatformArchive,
    RecordedPlatformClient,
)
from yuliang.modules.connectors.base import GatewayContext, ObjectRef
from yuliang.modules.connectors.http import HttpExchange, OperationSpec, PlatformHttpClient
from yuliang.modules.connectors.outcomes import BusinessVerdict
from yuliang.modules.connectors.redaction import ArchivePolicy, fingerprint
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    object_id: str
    value: int
    notes: str = "synthetic-private-customer-note"


def setup(cap: dict[str, Any]) -> tuple[GatewayContext, OperationSpec, ObjectRef, PlatformArchive]:
    binding = cap["binding"]
    ctx = GatewayContext(
        cap["identity"],
        cap["shop"],
        binding.account_id,
        str(binding.account_id),
        "adq",
        "tencent",
        "adq",
        uuid4(),
        uuid4(),
        binding.id,
        1,
        binding.catalog_id,
        binding.catalog_sha256,
    )
    spec = OperationSpec(
        "adgroups.update",
        ctx.catalog_id,
        ctx.catalog_sha256,
        "adq",
        "tencent",
        "adq",
        "api.e.qq.com",
        "POST",
        "/v3.0/adgroups/update",
        "WRITE",
        Parameters,
        account_parameter="account_id",
        target_parameter="object_id",
    )
    target = ObjectRef(
        tenant_id=ctx.identity.tenant_id,
        account_id=ctx.account_id,
        connector_id="adq",
        provider="tencent",
        product_line="adq",
        native_id="456",
    )
    return (
        ctx,
        spec,
        target,
        PlatformArchive(cap["engine"], SecretStr("synthetic-audit-key-only-at-least-32-chars")),
    )


def signer(request: httpx.Request, credential: SecretStr) -> httpx.Request:
    request.headers["Authorization"] = "Bearer " + credential.get_secret_value()
    return request


def decoder(response: HttpExchange) -> BusinessVerdict:
    data = json.loads(response.body)
    return BusinessVerdict(
        "CONFIRMED_SUCCESS" if data.get("code") == 0 else "CONFIRMED_REJECTION",
        "PARAMETER",
        data.get("trace_id"),
    )


def parameters(ctx: GatewayContext) -> dict[str, Any]:
    return {"account_id": ctx.native_account_id, "object_id": "456", "value": 100}


async def test_request_response_trace_audit_and_private_projection(
    capability: dict[str, Any],
) -> None:
    cap = capability
    ctx, spec, target, archive = setup(cap)
    outbound = uuid4()
    calls = []

    def platform(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "trace_id": "synthetic-platform-trace",
                "access_token": "private-token",
                "data": {
                    "budget_minor": 100,
                    "receiver_name": "Private Person",
                    "phone": "13800000000",
                    "unknownNested": "private-other",
                },
            },
        )

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(platform)),
        archive,
        decoder=decoder,
    )
    result = await client.send(
        ctx, spec, outbound, parameters(ctx), SecretStr("private-credential"), target=target
    )
    assert (
        result.metadata["externalOutcome"] == "ACCEPTED" and result.metadata["networkAttempts"] == 1
    )
    assert result.metadata["requestId"] == str(ctx.request_id) and "payload" not in result.metadata
    with business_transaction(cap["engine"], cap["identity"]) as session:
        root = (
            session.execute(text("SELECT * FROM platform_requests WHERE id=:id"), {"id": outbound})
            .mappings()
            .one()
        )
        events = (
            session.execute(
                text("SELECT * FROM platform_raw_payloads WHERE outbound_id=:id ORDER BY id"),
                {"id": outbound},
            )
            .mappings()
            .all()
        )
        audits = (
            session.execute(
                text(
                    "SELECT id FROM audit_logs WHERE request_id=:request "
                    "AND action='platform.request'"
                ),
                {"request": ctx.request_id},
            )
            .scalars()
            .all()
        )
    assert len(events) == 2 and root["audit_id"] in audits
    assert events[-1]["trace_digest"] == fingerprint(
        archive.key, "platform-trace-v1", b"synthetic-platform-trace"
    )
    saved = str(root["request_json"]) + str(events[-1]["payload"])
    assert all(
        value not in saved
        for value in [
            "private-credential",
            "private-token",
            "Private Person",
            "13800000000",
            "private-other",
            "synthetic-private-customer-note",
        ]
    )
    assert events[-1]["payload"]["data"] == {"code": 0, "data": {"budget_minor": 100}}
    replay = await client.send(
        replace(ctx, request_id=uuid4()),
        spec,
        outbound,
        parameters(ctx),
        SecretStr("other-credential"),
        target=target,
    )
    assert (
        replay.exchange is None
        and replay.metadata["requestId"] == str(ctx.request_id)
        and len(calls) == 1
    )
    with pytest.raises(ApiError):
        await client.send(
            ctx,
            spec,
            outbound,
            parameters(ctx) | {"value": 101},
            SecretStr("credential"),
            target=target,
        )
    assert len(calls) == 1


@pytest.mark.parametrize("status", [200, 429, 500])
async def test_http_without_business_proof_does_not_claim_write_success(
    capability: dict[str, Any], status: int
) -> None:
    cap = capability
    ctx, spec, target, archive = setup(cap)

    def platform(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"Retry-After": "15"}, json={"mystery": "private"})

    result = await RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(platform)), archive
    ).send(ctx, spec, uuid4(), parameters(ctx), SecretStr("credential"), target=target)
    assert result.metadata["externalOutcome"] == "UNKNOWN"
    assert (
        result.metadata["category"]
        == {200: "INVALID_RESPONSE", 429: "RATE_LIMITED", 500: "UPSTREAM_ERROR"}[status]
    )
    if status == 429:
        assert result.metadata["retryAfterSeconds"] == 15


async def test_lost_response_keeps_original_unknown_and_replay_never_resends(
    capability: dict[str, Any],
) -> None:
    cap = capability
    ctx, spec, target, archive = setup(cap)
    outbound = uuid4()
    calls = []

    def timeout(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("synthetic response loss", request=request)

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(timeout)), archive
    )
    first = await client.send(
        ctx, spec, outbound, parameters(ctx), SecretStr("credential"), target=target
    )
    assert (
        first.metadata["externalOutcome"] == "UNKNOWN" and first.metadata["category"] == "TIMEOUT"
    )
    second = await client.send(
        ctx, spec, outbound, parameters(ctx), SecretStr("credential"), target=target
    )
    assert second.metadata == first.metadata and len(calls) == 1


async def test_request_archive_failure_rolls_back_audit_and_prevents_http(
    capability: dict[str, Any],
) -> None:
    cap = capability
    ctx, spec, target, archive = setup(cap)
    outbound = uuid4()
    calls = []

    def platform(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    with cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(text("REVOKE INSERT ON platform_requests FROM app_login"))
    try:
        with pytest.raises(ArchiveUnavailable) as failed:
            await RecordedPlatformClient(
                PlatformHttpClient(signer, transport=httpx.MockTransport(platform)), archive
            ).send(ctx, spec, outbound, parameters(ctx), SecretStr("credential"), target=target)
        assert failed.value.dispatch == "NOT_SENT" and not calls
    finally:
        with cap["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("GRANT INSERT ON platform_requests TO app_login"))
    with business_transaction(cap["engine"], cap["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE id=:id"), {"id": outbound}
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM audit_logs WHERE request_id=:id"), {"id": ctx.request_id}
            ).scalar_one()
            == 0
        )


async def test_response_archive_failure_preserves_started_fact_and_blocks_resend(
    capability: dict[str, Any],
) -> None:
    cap = capability
    ctx, spec, target, archive = setup(cap)
    outbound = uuid4()
    calls = []

    def platform(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        with cap["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("REVOKE INSERT ON platform_raw_payloads FROM app_login"))
        return httpx.Response(200, json={"code": 0})

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(platform)),
        archive,
        decoder=decoder,
    )
    try:
        with pytest.raises(ArchiveUnavailable) as failed:
            await client.send(
                ctx, spec, outbound, parameters(ctx), SecretStr("credential"), target=target
            )
        assert failed.value.dispatch == "RESPONDED"
    finally:
        with cap["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("GRANT INSERT ON platform_raw_payloads TO app_login"))
    assert archive.summary(ctx, outbound)["externalOutcome"] == "UNKNOWN"
    replay = await client.send(
        ctx, spec, outbound, parameters(ctx), SecretStr("credential"), target=target
    )
    assert replay.metadata["externalOutcome"] == "UNKNOWN" and len(calls) == 1


async def test_cross_identity_tenant_and_runtime_mutation_are_denied(
    capability: dict[str, Any],
) -> None:
    cap = capability
    ctx, spec, target, archive = setup(cap)
    outbound = uuid4()

    def platform(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": 1})

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(platform)),
        archive,
        decoder=decoder,
    )
    result = await client.send(
        ctx, spec, outbound, parameters(ctx), SecretStr("credential"), target=target
    )
    assert (
        result.metadata["externalOutcome"] == "REJECTED"
        and result.metadata["category"] == "PARAMETER_REJECTED"
    )
    for identity in [
        VerifiedIdentity(cap["tenant"], cap["approver_member"], cap["approver"]),
        VerifiedIdentity(cap["tenant"] + 10000, cap["member"], cap["user"]),
    ]:
        with pytest.raises(ApiError):
            archive.summary(replace(ctx, identity=identity), outbound)
        with business_transaction(cap["engine"], identity) as session:
            assert session.execute(text("SELECT count(*) FROM platform_requests")).scalar_one() == 0
            assert (
                session.execute(text("SELECT count(*) FROM platform_raw_payloads")).scalar_one()
                == 0
            )
    for statement in [
        "UPDATE platform_requests SET effect='READ'",
        "DELETE FROM platform_raw_payloads",
    ]:
        with (
            pytest.raises(DBAPIError) as failed,
            business_transaction(cap["engine"], cap["identity"]) as session,
        ):
            session.execute(text(statement))
        assert getattr(failed.value.orig, "sqlstate", None) == "42501"


async def test_read_retries_archive_each_wire_attempt_and_cannot_relabel_a_write(
    capability: dict[str, Any],
) -> None:
    cap = capability
    ctx, spec, target, archive = setup(cap)
    calls = []

    def platform(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500 if len(calls) == 1 else 200, json={"code": 0})

    async def no_wait(seconds: float) -> None:
        pass

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(platform), sleep=no_wait),
        archive,
        decoder=decoder,
    )
    with pytest.raises(ApiError):
        await client.send(
            ctx,
            replace(spec, effect="READ", max_read_attempts=2, idempotent_read=True),
            uuid4(),
            parameters(ctx),
            SecretStr("credential"),
            target=target,
        )
    assert not calls
    read = cap["binding"].model_copy(
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
    with cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_binding(owner, read)
    readctx = replace(ctx, binding_id=read.id, catalog_id=read.catalog_id)
    readspec = replace(
        spec,
        effect="READ",
        method="GET",
        path=read.normalized_path,
        catalog_id=read.catalog_id,
        max_read_attempts=2,
        idempotent_read=True,
    )
    outbound = uuid4()
    result = await client.send(
        readctx, readspec, outbound, parameters(ctx), SecretStr("credential"), target=target
    )
    assert result.metadata["externalOutcome"] == "READ_SUCCEEDED" and len(calls) == 2
    with business_transaction(cap["engine"], cap["identity"]) as session:
        events = session.execute(
            text(
                "SELECT phase,network_attempt,http_status FROM platform_raw_payloads "
                "WHERE outbound_id=:id ORDER BY id"
            ),
            {"id": outbound},
        ).all()
    assert [tuple(row) for row in events] == [
        ("ATTEMPT", 1, None),
        ("RESPONSE", 1, 500),
        ("ATTEMPT", 2, None),
        ("RESPONSE", 2, 200),
    ]


@pytest.mark.parametrize("started", [False, True])
async def test_existing_intent_or_started_marker_never_automatically_resends(
    capability: dict[str, Any],
    started: bool,
) -> None:
    cap = capability
    ctx, spec, target, archive = setup(cap)
    outbound = uuid4()
    expanded = Parameters.model_validate(parameters(ctx)).model_dump()
    assert archive.begin(ctx, spec, outbound, expanded, ArchivePolicy(), target, None, None)
    if started:
        archive.append(ctx, spec, outbound, 1, "ATTEMPT", "SENDING")

    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不重发已有出站请求")

    result = await RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(never)), archive
    ).send(ctx, spec, outbound, parameters(ctx), SecretStr("credential"), target=target)
    assert result.metadata["externalOutcome"] == ("UNKNOWN" if started else "NOT_SENT")


async def test_inflight_response_survives_member_revocation_but_no_new_send_is_allowed(
    capability: dict[str, Any],
) -> None:
    cap = capability
    ctx, spec, target, archive = setup(cap)
    calls = []

    def platform(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        with cap["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text("UPDATE memberships SET status='REMOVED' WHERE id=:id"), {"id": cap["member"]}
            )
        return httpx.Response(200, json={"code": 0})

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(platform)),
        archive,
        decoder=decoder,
    )
    result = await client.send(
        ctx, spec, uuid4(), parameters(ctx), SecretStr("credential"), target=target
    )
    assert result.metadata["externalOutcome"] == "ACCEPTED"
    with pytest.raises(ApiError):
        await client.send(
            ctx, spec, uuid4(), parameters(ctx), SecretStr("credential"), target=target
        )
    assert len(calls) == 1
