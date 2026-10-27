"""证据不能跳级、跨读写混用或凭单次验证许可升级普通能力。"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.capability.models import (
    CONNECTOR_CONTEXTS,
    Binding,
    Evidence,
    Phase,
    ValidationPermit,
    aggregate,
)

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def target(*, kind: str = "WRITE", connector: str = "adq") -> Binding:
    provider, product = CONNECTOR_CONTEXTS[connector]
    return Binding.model_validate(
        dict(
            id=uuid4(),
            tenant_id=1,
            account_id=uuid4(),
            connector_id=connector,
            provider=provider,
            product_line=product,
            action="budget",
            operation_kind=kind,
            catalog_sha256="a" * 64,
            catalog_id="b" * 20,
            revision=1,
            http_method="POST" if kind == "WRITE" else "GET",
            normalized_path="/v3.0/adgroups/update",
            permission_code="budget.edit",
        )
    )


def proofs(binding: Binding) -> list[Evidence]:
    kinds = [
        "DOCUMENTATION",
        "SCOPE_GRANT",
        "ACCOUNT_AUTH",
        "ADAPTER_INTEGRATION",
        "WRITE_SCHEMA" if binding.operation_kind == "WRITE" else "READ_SCHEMA",
        "MATCHED_RECEIPT" if binding.operation_kind == "WRITE" else "READ_RESULT",
    ]
    return [
        Evidence.model_validate(
            dict(
                id=uuid4(),
                binding_id=binding.id,
                sequence=index + 1,
                phase=phase,
                result="PASS",
                proof_kind=kinds[index],
                proof_ref=f"synthetic/{phase}",
                checked_by="SYNTHETIC_TEST",
                checked_at=NOW - timedelta(minutes=1),
                expires_at=NOW + timedelta(hours=1),
            )
        )
        for index, phase in enumerate(Phase)
    ]


@pytest.mark.parametrize("phase", list(Phase))
def test_expired_predecessor_invalidates_every_later_stage(phase: Phase) -> None:
    binding = target()
    items = proofs(binding)
    index = list(Phase).index(phase)
    items[index] = items[index].model_copy(update={"expires_at": NOW})
    result = aggregate(binding, items, now=NOW)
    assert result["status"] != "AUTHORIZED" and result["operations"] == []
    assert all(not result["stages"][stage.value]["effective"] for stage in list(Phase)[index:])


def test_no_jumping_or_unknown_result_bootstrap() -> None:
    binding = target()
    items = proofs(binding)
    assert aggregate(binding, items[1:], now=NOW)["operations"] == []
    first = aggregate(binding, items[:-1], now=NOW)
    assert (
        first["validationReady"]
        and first["status"] == "NOT_INTEGRATED"
        and first["operations"] == []
    )
    items[-1] = items[-1].model_copy(update={"result": "UNKNOWN"})
    assert not aggregate(binding, items, now=NOW)["validationReady"]


def test_read_results_enable_only_read_operation_and_never_write() -> None:
    read = target(kind="READ")
    result = aggregate(read, proofs(read), now=NOW)
    assert result["status"] == "AUTHORIZED" and result["operations"][0]["kind"] == "READ"
    write = target()
    assert aggregate(write, proofs(read), now=NOW)["status"] == "ERROR"
    wrong_kind = [item.model_copy(update={"binding_id": write.id}) for item in proofs(read)]
    assert aggregate(write, wrong_kind, now=NOW)["status"] == "ERROR"
    assert aggregate(write, proofs(write), now=NOW)["status"] == "AUTHORIZED"


@pytest.mark.parametrize("connector", ["qianchuan", "douyinshop", "juguang", "redshop", "beans"])
def test_out_of_scope_provider_is_closed_even_with_all_synthetic_proofs(connector: str) -> None:
    binding = target(connector=connector)
    result = aggregate(binding, proofs(binding), now=NOW)
    assert result["status"] == "NOT_INTEGRATED" and result["operations"] == []


def test_six_availability_states_and_revocation() -> None:
    binding = target()
    items = proofs(binding)
    states = {
        aggregate(binding, items, now=NOW)["status"],
        aggregate(binding, [], now=NOW)["status"],
        aggregate(binding, items, now=NOW, permitted=False)["status"],
        aggregate(binding, items, now=NOW, applicable=False)["status"],
        aggregate(binding, items, now=NOW, account_authorized=False)["status"],
    }
    items[3] = items[3].model_copy(update={"result": "FAIL"})
    states.add(aggregate(binding, items, now=NOW)["status"])
    assert states == {
        "AUTHORIZED",
        "NOT_INTEGRATED",
        "FORBIDDEN",
        "NOT_APPLICABLE",
        "UNAUTHORIZED",
        "ERROR",
    }


def test_latest_failure_and_same_timestamp_sequence_are_authoritative() -> None:
    binding = target()
    items = proofs(binding)
    failure = items[0].model_copy(update={"id": uuid4(), "sequence": 99, "result": "FAIL"})
    assert aggregate(binding, items + [failure], now=NOW)["status"] == "ERROR"
    conflicting = failure.model_copy(update={"sequence": items[0].sequence})
    with pytest.raises(ValueError):
        aggregate(binding, items + [conflicting], now=NOW)
    with pytest.raises(ValueError):
        aggregate(binding, items, now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    "change",
    [
        {"product_line": "shop"},
        {"provider": "ocean"},
        {"http_method": "GET"},
        {"permission_code": "snapshot.read"},
        {"tenant_id": True},
        {"revision": 0},
        {"normalized_path": "https://wrong/path"},
    ],
)
def test_binding_context_cannot_be_crossed(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Binding.model_validate(target().model_dump() | change)


def test_renewed_predecessor_cannot_reuse_older_successor_proofs() -> None:
    binding = target()
    items = proofs(binding)
    renewed = items[1].model_copy(update={"id": uuid4(), "sequence": 99, "checked_at": NOW})
    result = aggregate(binding, items + [renewed], now=NOW)
    assert result["status"] != "AUTHORIZED" and not result["validationReady"]
    assert result["stages"][Phase.SCOPE.value]["effective"]
    assert not result["stages"][Phase.ACCOUNT.value]["effective"]


@pytest.mark.parametrize(
    "change",
    [
        {"self_approval": True},
        {"max_amount_minor": True},
        {"max_amount_minor": -1},
        {"max_amount_minor": 9007199254740992},
        {"request_digest": "bad"},
        {"expires_at": NOW - timedelta(seconds=1)},
    ],
)
def test_validation_permit_requires_distinct_bounded_manual_approval(
    change: dict[str, Any],
) -> None:
    actor = uuid4()
    data = dict(
        id=uuid4(),
        binding_id=uuid4(),
        requester_id=actor,
        approved_by=uuid4(),
        approval_ref="synthetic/approval",
        request_key=uuid4(),
        request_digest="a" * 64,
        max_amount_minor=100,
        starts_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    if change.get("self_approval"):
        data["approved_by"] = actor
    else:
        data.update(change)
    with pytest.raises(ValidationError):
        ValidationPermit.model_validate(data)
