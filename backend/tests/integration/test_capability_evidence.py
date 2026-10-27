"""真实PG验证能力隔离、撤权、灰度首写许可和消费竞争；证据全部为合成测试。"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.catalog import import_catalog, load_catalog
from yuliang.modules.capability.models import Binding, Evidence, Phase, ValidationPermit
from yuliang.modules.capability.service import (
    consume_validation,
    evaluate_action,
    evaluate_binding,
    record_evidence,
    register_binding,
    register_validation_permit,
)
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError

from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def capability(login_api: dict[str, Any]) -> Iterator[dict[str, Any]]:
    api = login_api
    engine = create_engine(
        api["database"]["migration"].replace("postgresql://", "postgresql+psycopg://"),
        hide_parameters=True,
    )
    bundle = load_catalog(ROOT)
    account, approver, member = uuid4(), uuid4(), uuid4()
    binding = Binding.model_validate(
        dict(
            id=uuid4(),
            tenant_id=api["tenant"],
            account_id=account,
            connector_id="adq",
            provider="tencent",
            product_line="adq",
            action="budget",
            operation_kind="WRITE",
            catalog_sha256=bundle.sha256,
            catalog_id="2787a70612d48bd92d4b",
            revision=1,
            http_method="POST",
            normalized_path="/v3.0/adgroups/update",
            permission_code="budget.edit",
        )
    )
    with engine.begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        import_catalog(owner, bundle)
        owner.execute(
            text(
                "INSERT INTO ad_accounts(id,tenant_id,provider,product_line,native_id,name,status) "
                "VALUES(:id,:tenant,'tencent','adq',:native,'Synthetic','AUTHORIZED')"
            ),
            {"id": account, "tenant": api["tenant"], "native": str(account)},
        )
        owner.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                "VALUES(:tenant,:shop,:account)"
            ),
            {"tenant": api["tenant"], "shop": api["shop"], "account": account},
        )
        owner.execute(
            text(
                "INSERT INTO users(id,email,display_name) VALUES(:id,:email,'Synthetic approver')"
            ),
            {"id": approver, "email": f"{approver}@example.invalid"},
        )
        owner.execute(
            text("INSERT INTO memberships(id,tenant_id,user_id) VALUES(:id,:tenant,:user)"),
            {"id": member, "tenant": api["tenant"], "user": approver},
        )
        owner.execute(
            text(
                "INSERT INTO role_grants(tenant_id,membership_id,role_id) "
                "SELECT :tenant,:member,id FROM roles WHERE tenant_id=:tenant AND code='OWNER'"
            ),
            {"tenant": api["tenant"], "member": member},
        )
        owner.execute(
            text(
                "INSERT INTO data_scopes(tenant_id,membership_id,scope_type) "
                "VALUES(:tenant,:member,'ALL_TENANT')"
            ),
            {"tenant": api["tenant"], "member": member},
        )
        register_binding(owner, binding)
    now = datetime.now(UTC)
    permit = ValidationPermit(
        id=uuid4(),
        binding_id=binding.id,
        requester_id=api["user"],
        approved_by=approver,
        approval_ref="synthetic/approval",
        request_key=uuid4(),
        request_digest="a" * 64,
        max_amount_minor=100,
        starts_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    cap = api | {
        "maintenance": engine,
        "engine": api["app"].state.identity_service.app,
        "binding": binding,
        "identity": VerifiedIdentity(api["tenant"], api["member"], api["user"]),
        "approver": approver,
        "approver_member": member,
        "permit": permit,
        "now": now,
        "bundle": bundle,
    }
    try:
        yield cap
    finally:
        engine.dispose()


def populate(
    cap: dict[str, Any], *, execution: bool = False, target: Binding | None = None
) -> None:
    binding = target or cap["binding"]
    kinds = [
        "DOCUMENTATION",
        "SCOPE_GRANT",
        "ACCOUNT_AUTH",
        "ADAPTER_INTEGRATION",
        "WRITE_SCHEMA" if binding.operation_kind == "WRITE" else "READ_SCHEMA",
        "MATCHED_RECEIPT" if binding.operation_kind == "WRITE" else "READ_RESULT",
    ]
    with cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        for index, phase in enumerate(Phase):
            if phase == Phase.EXECUTION and not execution:
                continue
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
                        proof_ref=f"synthetic/{phase}",
                        checked_by="SYNTHETIC_TEST",
                        checked_at=cap["now"] - timedelta(seconds=20),
                        expires_at=cap["now"] + timedelta(hours=1),
                    )
                ),
            )


def grant(cap: dict[str, Any], permit: ValidationPermit | None = None) -> None:
    with cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_validation_permit(owner, permit or cap["permit"])


def consume(cap: dict[str, Any], **changes: Any) -> bool:
    permit = cap["permit"]
    values = (
        dict(
            permit_id=permit.id,
            shop_id=cap["shop"],
            request_key=permit.request_key,
            request_digest=permit.request_digest,
            amount_minor=50,
        )
        | changes
    )
    with business_transaction(cap["engine"], cap["identity"]) as session:
        return consume_validation(session, **values)


def test_first_five_allow_only_one_atomic_validation_not_ordinary_write(
    capability: dict[str, Any],
) -> None:
    cap = capability
    populate(cap)
    grant(cap)
    with business_transaction(cap["engine"], cap["identity"]) as session:
        before = evaluate_binding(session, cap["identity"], cap["shop"], cap["binding"].id)
    assert (
        before["status"] == "NOT_INTEGRATED"
        and before["validationReady"]
        and before["operations"] == []
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(cap), range(2)))
    assert sorted(results) == [False, True]
    with business_transaction(cap["engine"], cap["identity"]) as session:
        after = evaluate_binding(session, cap["identity"], cap["shop"], cap["binding"].id)
    assert after["status"] == "NOT_INTEGRATED" and after["operations"] == []
    assert not after["validationReady"]
    another = cap["permit"].model_copy(update={"id": uuid4(), "request_key": uuid4()})
    grant(cap, another)
    assert not consume(cap, permit_id=another.id, request_key=another.request_key)


@pytest.mark.parametrize(
    "case", ["key", "digest", "amount", "shop", "bool_amount", "missing_phase"]
)
def test_validation_rejects_unbound_inputs_without_consuming(
    capability: dict[str, Any], case: str
) -> None:
    cap = capability
    if case != "missing_phase":
        populate(cap)
    grant(cap)
    options: dict[str, dict[str, Any]] = {
        "key": {"request_key": uuid4()},
        "digest": {"request_digest": "b" * 64},
        "amount": {"amount_minor": 101},
        "shop": {"shop_id": uuid4()},
        "bool_amount": {"amount_minor": True},
        "missing_phase": {},
    }
    changes = options[case]
    assert not consume(cap, **changes)
    with cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            owner.execute(
                text("SELECT used_at FROM capability_validation_permits WHERE id=:id"),
                {"id": cap["permit"].id},
            ).scalar_one()
            is None
        )


@pytest.mark.parametrize(
    "case",
    [
        "account_revoked",
        "approver_removed",
        "approver_scope_removed",
        "approver_disabled",
        "actor_scope_removed",
        "expired",
        "future",
        "unknown_result",
        "catalog_changed",
        "renewed_predecessor",
        "proof_expired",
    ],
)
def test_revocations_and_unknown_freeze_first_write(capability: dict[str, Any], case: str) -> None:
    cap = capability
    populate(cap)
    permit = cap["permit"]
    if case == "expired":
        permit = permit.model_copy(
            update={
                "starts_at": cap["now"] - timedelta(hours=2),
                "expires_at": cap["now"] - timedelta(hours=1),
            }
        )
    elif case == "future":
        permit = permit.model_copy(update={"starts_at": cap["now"] + timedelta(minutes=1)})
    grant(cap, permit)
    with cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if case == "account_revoked":
            owner.execute(
                text("UPDATE ad_accounts SET status='REVOKED' WHERE id=:id"),
                {"id": cap["binding"].account_id},
            )
        elif case == "approver_removed":
            owner.execute(
                text("UPDATE memberships SET status='REMOVED' WHERE id=:id"),
                {"id": cap["approver_member"]},
            )
        elif case == "actor_scope_removed":
            owner.execute(
                text("DELETE FROM data_scopes WHERE membership_id=:id"), {"id": cap["member"]}
            )
        elif case == "approver_scope_removed":
            owner.execute(
                text("DELETE FROM data_scopes WHERE membership_id=:id"),
                {"id": cap["approver_member"]},
            )
        elif case == "renewed_predecessor":
            record_evidence(
                owner,
                cap["binding"].id,
                Evidence(
                    id=uuid4(),
                    binding_id=cap["binding"].id,
                    phase=Phase.SCOPE,
                    result="PASS",
                    proof_kind="SCOPE_GRANT",
                    proof_ref="synthetic/new-scope",
                    checked_by="SYNTHETIC_TEST",
                    checked_at=cap["now"],
                    expires_at=cap["now"] + timedelta(hours=1),
                ),
            )
        elif case == "approver_disabled":
            owner.execute(
                text("UPDATE users SET status='DISABLED' WHERE id=:id"), {"id": cap["approver"]}
            )
        elif case == "proof_expired":
            record_evidence(
                owner,
                cap["binding"].id,
                Evidence(
                    id=uuid4(),
                    binding_id=cap["binding"].id,
                    phase=Phase.SCHEMA,
                    result="PASS",
                    proof_kind="WRITE_SCHEMA",
                    proof_ref="synthetic/expired-schema",
                    checked_by="SYNTHETIC_TEST",
                    checked_at=cap["now"] - timedelta(seconds=2),
                    expires_at=cap["now"] - timedelta(seconds=1),
                ),
            )
        elif case == "catalog_changed":
            owner.execute(text("DELETE FROM catalog_head"))
        elif case == "unknown_result":
            record_evidence(
                owner,
                cap["binding"].id,
                Evidence(
                    id=uuid4(),
                    binding_id=cap["binding"].id,
                    phase=Phase.EXECUTION,
                    result="UNKNOWN",
                    proof_kind="MATCHED_RECEIPT",
                    proof_ref="synthetic/unknown",
                    checked_by="SYNTHETIC_TEST",
                    checked_at=cap["now"],
                    expires_at=cap["now"] + timedelta(hours=1),
                ),
            )
    assert not consume(cap)


def test_different_bootstrap_keys_still_allow_only_one_unreconciled_attempt(
    capability: dict[str, Any],
) -> None:
    cap = capability
    populate(cap)
    first = cap["permit"]
    second = first.model_copy(update={"id": uuid4(), "request_key": uuid4()})
    grant(cap, first)
    grant(cap, second)

    def run(permit: ValidationPermit) -> bool:
        return consume(cap, permit_id=permit.id, request_key=permit.request_key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [first, second]))
    assert sorted(results) == [False, True]


def test_read_and_write_use_separate_real_result_proofs(capability: dict[str, Any]) -> None:
    cap = capability
    read = cap["binding"].model_copy(
        update={
            "id": uuid4(),
            "catalog_id": "90b086afd649f5dc0600",
            "normalized_path": "/v3.0/adgroups/get",
            "operation_kind": "READ",
            "http_method": "GET",
            "action": "analyze",
            "permission_code": "overview.read",
        }
    )
    with cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_binding(owner, read)
    populate(cap, execution=True, target=read)
    populate(cap)
    with business_transaction(cap["engine"], cap["identity"]) as session:
        result = evaluate_binding(session, cap["identity"], cap["shop"], read.id)
        assert result["status"] == "AUTHORIZED" and result["operations"][0]["kind"] == "READ"
        assert (
            evaluate_binding(session, cap["identity"], cap["shop"], cap["binding"].id)["operations"]
            == []
        )


def test_complete_write_can_be_revoked_and_new_context_needs_fresh_proofs(
    capability: dict[str, Any],
) -> None:
    cap = capability
    populate(cap, execution=True)
    with business_transaction(cap["engine"], cap["identity"]) as session:
        assert (
            evaluate_action(
                session,
                cap["identity"],
                cap["shop"],
                cap["binding"].account_id,
                "adq",
                "adq",
                "budget",
            )["status"]
            == "AUTHORIZED"
        )
    replacement = cap["binding"].model_copy(update={"id": uuid4(), "revision": 2})
    with cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE capability_bindings SET active=false WHERE id=:id"),
            {"id": cap["binding"].id},
        )
        register_binding(owner, replacement)
    with business_transaction(cap["engine"], cap["identity"]) as session:
        assert (
            evaluate_binding(session, cap["identity"], cap["shop"], replacement.id)["operations"]
            == []
        )
        with pytest.raises(ApiError):
            evaluate_binding(session, cap["identity"], cap["shop"], cap["binding"].id)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE capability_bindings SET active=false",
        "DELETE FROM capability_assertions",
        "UPDATE capability_validation_permits SET used_at=NULL",
        "UPDATE capability_assertions SET result='PASS'",
    ],
)
def test_runtime_cannot_self_authorize_or_replenish_permit(
    capability: dict[str, Any], statement: str
) -> None:
    cap = capability
    populate(cap)
    grant(cap)
    with (
        pytest.raises(DBAPIError) as rejected,
        business_transaction(cap["engine"], cap["identity"]) as session,
    ):
        session.execute(text(statement))
    assert getattr(rejected.value.orig, "sqlstate", None) == "42501"


def test_cross_tenant_rls_and_cross_product_binding_rejected(capability: dict[str, Any]) -> None:
    cap = capability
    populate(cap)
    grant(cap)
    other = VerifiedIdentity(cap["tenant"] + 100000, cap["member"], cap["user"])
    with business_transaction(cap["engine"], other) as session:
        for statement in [
            "SELECT count(*) FROM capability_bindings",
            "SELECT count(*) FROM capability_assertions",
            "SELECT count(*) FROM capability_validation_permits",
        ]:
            assert session.execute(text(statement)).scalar_one() == 0
        assert not consume_validation(
            session, cap["permit"].id, cap["shop"], cap["permit"].request_key, "a" * 64, 50
        )
    bad = cap["binding"].model_copy(
        update={"id": uuid4(), "connector_id": "wxpromo", "product_line": "shop"}
    )
    with pytest.raises(DBAPIError), cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_binding(owner, bad)


def test_bindings_and_proof_ids_are_immutable_and_read_proof_cannot_be_write(
    capability: dict[str, Any],
) -> None:
    cap = capability
    with pytest.raises(DBAPIError), cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE capability_bindings SET action='pause' WHERE id=:id"),
            {"id": cap["binding"].id},
        )
    evidence = Evidence(
        id=uuid4(),
        binding_id=cap["binding"].id,
        phase=Phase.SCHEMA,
        result="PASS",
        proof_kind="WRITE_SCHEMA",
        proof_ref="synthetic/schema",
        checked_by="SYNTHETIC_TEST",
        checked_at=cap["now"],
        expires_at=cap["now"] + timedelta(hours=1),
    )
    with cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        first = record_evidence(owner, cap["binding"].id, evidence)
        assert record_evidence(owner, cap["binding"].id, evidence).sequence == first.sequence
        with pytest.raises(ValueError):
            record_evidence(
                owner, cap["binding"].id, evidence.model_copy(update={"result": "FAIL"})
            )
        with pytest.raises(ValueError):
            record_evidence(
                owner,
                cap["binding"].id,
                evidence.model_copy(update={"id": uuid4(), "proof_kind": "READ_SCHEMA"}),
            )
