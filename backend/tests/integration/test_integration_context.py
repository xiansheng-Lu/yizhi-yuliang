"""真实PG/会话HTTP上下文；所有身份、授权、映射及阶段依据均为合成输入。"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.catalog.service import link_authorization
from yuliang.modules.configuration.integration_evidence import register_operation
from yuliang.modules.configuration.integration_models import OperationDeclaration
from yuliang.modules.configuration.integration_service import IntegrationService
from yuliang.platform.db import VerifiedIdentity, business_transaction

from .test_capability_evidence import capability as capability
from .test_capability_evidence import populate
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api

pytestmark = pytest.mark.integration
URL = "/v1/delivery/integrations/context"


def declaration(
    data: dict[str, Any], *, write: bool = False, expired: bool = False
) -> OperationDeclaration:
    binding = data["binding"] if write else data["read_binding"]
    now = datetime.now(UTC)
    return OperationDeclaration(
        binding_id=binding.id,
        binding_version=binding.revision,
        resource="adgroups",
        editable_fields=("budget",) if write else (),
        authorization_id=data["authorization"],
        authorization_version=1,
        required_scopes=("write",) if write else ("read",),
        proof_ref="synthetic/M3-11a/resource",
        checked_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1) if expired else now + timedelta(minutes=10),
    )


def register(data: dict[str, Any], proof: OperationDeclaration) -> None:
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert register_operation(owner, data["tenant"], proof) == proof.id


def test_http_actual_identifiers_links_and_seven_stage_operation_context(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    register(data, declaration(data))
    populate(data, execution=True)
    register(data, declaration(data, write=True))
    response = data["client"].get(URL, params={"productLine": "adq"})
    assert response.status_code == 200, response.text
    view = response.json()["data"]
    account, link = view["accounts"][0], view["links"][0]
    operation = next(item for item in view["operations"] if item["kind"] == "READ")
    assert account["id"] == str(data["read_binding"].account_id)
    assert account["nativeId"] == str(data["read_binding"].account_id)
    assert (account["provider"], account["productLine"]) == ("tencent", "adq")
    assert account["authorization"] == link["state"] == "AUTHORIZED"
    assert operation["accountIds"] == [account["id"]] and operation["permission"] == "overview.read"
    assert operation["editableFields"] == [] and operation["kind"] == "READ"
    write = next(item for item in view["operations"] if item["kind"] == "WRITE")
    assert not write["evidence"]["backend"]["verified"]
    assert len(operation["evidence"]) == 7
    assert all(
        operation["evidence"][key]["verified"]
        for key in ("directory", "path", "applicationScope", "account")
    )
    # 旧v3目录/阶段声明不是正式v1.3广告组读取实现；前端queryBlock因此拒绝。
    assert not operation["evidence"]["backend"]["verified"]
    assert not operation["evidence"]["requestSchema"]["verified"]
    assert not operation["evidence"]["execution"]["verified"]
    assert "private-access" not in response.text and response.headers["cache-control"] == "no-store"
    if target := os.getenv("YULIANG_INTEGRATION_CONTEXT_EVIDENCE"):
        Path(target).write_text(
            json.dumps(
                {
                    "context": view,
                    "identity": data["client"].get("/v1/auth/session").json()["data"],
                    "input": "SYNTHETIC_SOURCE_PROOFS_IDENTITIES_REAL_PG_COOKIE_HTTP",
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


@pytest.mark.parametrize(
    "change", ["expired_declaration", "revoked", "version", "scope", "expired_phase", "path"]
)
def test_expired_or_changed_sources_never_open_read_action(
    configuration_ready: dict[str, Any], change: str
) -> None:
    data = configuration_ready
    register(data, declaration(data, expired=change == "expired_declaration"))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        commands = {
            "revoked": "UPDATE provider_authorizations SET status='REVOKED' WHERE id=:auth",
            "version": "UPDATE provider_authorizations SET version=version+1 WHERE id=:auth",
            "scope": "UPDATE provider_authorizations SET scopes=ARRAY['other'] WHERE id=:auth",
            "expired_phase": "UPDATE capability_assertions "
            "SET expires_at=clock_timestamp()-interval '1 second' WHERE binding_id=:binding",
            "path": "UPDATE endpoint_catalog SET normalized_path='/different' "
            "WHERE catalog_id=:catalog",
        }
        if change in commands:
            owner.execute(
                text(commands[change]),
                {
                    "auth": data["authorization"],
                    "binding": data["read_binding"].id,
                    "catalog": data["read_binding"].catalog_id,
                },
            )
    try:
        context = IntegrationService(data["engine"], data["identity"]).context("adq")
        assert len(context.operations) == 1
        assert not context.operations[0].evidence["requestSchema"].verified
        if change == "revoked":
            assert (
                context.links[0].state == "BOUND"
                and context.accounts[0].authorization == "UNAUTHORIZED"
            )
    finally:
        if change == "path":
            # 目录是模块共享的冻结行；故障注入不能污染后续夹具。
            with data["maintenance"].begin() as owner:
                owner.execute(text("SET LOCAL ROLE app_owner"))
                owner.execute(
                    text("UPDATE endpoint_catalog SET normalized_path=:path WHERE catalog_id=:id"),
                    {
                        "path": data["read_binding"].normalized_path,
                        "id": data["read_binding"].catalog_id,
                    },
                )


def test_fully_proven_generic_write_binding_does_not_open_unimplemented_resource_commands(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    populate(data, execution=True)
    register(data, declaration(data, write=True))
    operation = IntegrationService(data["engine"], data["identity"]).context("adq").operations[0]
    assert operation.kind == "WRITE" and operation.editable_fields == ("budget",)
    assert operation.evidence["path"].verified and not operation.evidence["backend"].verified
    assert not operation.evidence["execution"].verified


def test_read_mapping_cannot_claim_editable_fields_and_runtime_cannot_self_grant(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    proof = declaration(data)
    with pytest.raises(ValueError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_operation(
            owner, data["tenant"], proof.model_copy(update={"editable_fields": ("budget",)})
        )
    register(data, proof)
    register(data, proof)
    with pytest.raises(ValueError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_operation(
            owner, data["tenant"], proof.model_copy(update={"proof_ref": "synthetic/changed"})
        )
    with (
        pytest.raises(DBAPIError) as denied,
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(text("DELETE FROM integration_operation_assertions"))
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"


def test_current_user_tenant_and_wxshop_business_objects_without_fake_ad_accounts(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    service = IntegrationService(data["engine"], data["identity"])
    shop = service.context("wxshop")
    assert (
        len(shop.shops) == 1 and shop.accounts == () and shop.links == () and shop.operations == ()
    )
    assert service.context("qianchuan").accounts == ()
    other = IntegrationService(
        data["engine"], VerifiedIdentity(data["tenant"] + 1, data["member"], data["user"])
    )
    assert other.context("adq").accounts == ()
    assert other.context("wxshop").shops == ()
    assert data["client"].get(URL + "?productLine=adq&accountId=" + str(uuid4())).status_code == 422
    assert data["client"].get(URL + "?productLine=adq&productLine=shop").status_code == 422
    assert (
        data["client"]
        .get(URL + "?productLine=adq", headers={"Origin": "https://other.invalid"})
        .status_code
        == 403
    )
    data["client"].cookies.clear()
    assert data["client"].get(URL + "?productLine=adq").status_code == 401


def test_many_to_many_graph_keeps_distinct_subjects_and_bound_account(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    register(data, declaration(data))
    entity, shop, account = uuid4(), uuid4(), uuid4()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO business_entities(id,tenant_id,name,kind) "
                "VALUES(:id,:tenant,'Synthetic second entity','MERCHANT')"
            ),
            {"id": entity, "tenant": data["tenant"]},
        )
        owner.execute(
            text(
                "INSERT INTO shops(id,tenant_id,entity_id,platform,native_id,name) "
                "VALUES(:id,:tenant,:entity,'wxshop',:native,'Synthetic second shop')"
            ),
            {"id": shop, "tenant": data["tenant"], "entity": entity, "native": str(shop)},
        )
        owner.execute(
            text(
                "INSERT INTO ad_accounts(id,tenant_id,entity_id,provider,product_line,"
                "native_id,name,status) VALUES(:id,:tenant,:entity,'tencent','adq',:native,"
                "'Synthetic bound account','BOUND')"
            ),
            {
                "id": account,
                "tenant": data["tenant"],
                "entity": entity,
                "native": "native-" + str(account),
            },
        )
        for target in (data["read_binding"].account_id, account):
            owner.execute(
                text(
                    "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                    "VALUES(:tenant,:shop,:account)"
                ),
                {"tenant": data["tenant"], "shop": shop, "account": target},
            )
    view = IntegrationService(data["engine"], data["identity"]).context("adq")
    assert (
        len(view.entities) == 2
        and len(view.shops) == 2
        and len(view.accounts) == 2
        and len(view.links) == 3
    )
    assert sum(link.state == "AUTHORIZED" for link in view.links) == 2
    bound = next(item for item in view.accounts if item.id == account)
    assert bound.authorization == "UNAUTHORIZED" and bound.native_id != str(bound.id)
    assert bound.business_entity_id == entity
    assert view.operations[0].account_ids == (data["read_binding"].account_id,)


def test_native_identifier_change_requires_independent_reverification(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    register(data, declaration(data))
    identifier = data["read_binding"].account_id
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE ad_accounts SET native_id='synthetic-native-v2' WHERE id=:id"),
            {"id": identifier},
        )
    service = IntegrationService(data["engine"], data["identity"])
    changed = service.context("adq")
    assert (
        changed.accounts[0].id == identifier
        and changed.accounts[0].native_id == "synthetic-native-v2"
    )
    assert changed.accounts[0].authorization == "UNAUTHORIZED" and changed.links[0].state == "BOUND"
    assert changed.operations == ()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        link_authorization(owner, data["authorization"], identifier, "synthetic/M3-11a/native-v2")
    assert service.context("adq").links[0].state == "AUTHORIZED"
    assert service.context("adq").operations == ()  # 原能力仍失效，关联重验不复活旧能力。


def test_historical_link_without_native_proof_stays_bound(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    register(data, declaration(data))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE authorization_account_links SET native_id_snapshot=NULL "
                "WHERE authorization_id=:id"
            ),
            {"id": data["authorization"]},
        )
    view = IntegrationService(data["engine"], data["identity"]).context("adq")
    assert view.links[0].state == "BOUND" and not view.operations[0].evidence["account"].verified


def test_context_lease_cannot_outlive_actual_authorization(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    deadline = datetime.now(UTC) + timedelta(seconds=12)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET expires_at=:expires WHERE id=:id"),
            {"expires": deadline, "id": data["authorization"]},
        )
    view = IntegrationService(data["engine"], data["identity"]).context("adq")
    assert view.links[0].state == "AUTHORIZED" and view.expires_at <= deadline


def test_operation_cannot_open_for_a_different_shop_permission_mask(
    configuration_ready: dict[str, Any],
) -> None:
    data = configuration_ready
    register(data, declaration(data))
    extra = uuid4()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO shops(id,tenant_id,entity_id,platform,native_id,name) "
                "VALUES(:id,:tenant,:entity,'wxshop',:native,'Synthetic restricted shop')"
            ),
            {
                "id": extra,
                "tenant": data["tenant"],
                "entity": data["context"].business_entity_id,
                "native": str(extra),
            },
        )
        owner.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                "VALUES(:tenant,:shop,:account)"
            ),
            {"tenant": data["tenant"], "shop": extra, "account": data["read_binding"].account_id},
        )
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
                "account": data["read_binding"].account_id,
            },
        )
        for identifier in (data["shop"], extra):
            owner.execute(
                text(
                    "INSERT INTO data_scopes(tenant_id,membership_id,scope_type,"
                    "shop_id,permissions_mask) "
                    "VALUES(:tenant,:member,'SHOP',:shop,:mask)"
                ),
                {
                    "tenant": data["tenant"],
                    "member": data["member"],
                    "shop": identifier,
                    "mask": ["snapshot.read"] if identifier == extra else None,
                },
            )
    context = IntegrationService(data["engine"], data["identity"]).context("adq")
    assert len(context.links) == 2 and context.accounts[0].authorization == "AUTHORIZED"
    assert not context.operations[0].evidence["account"].verified
    assert not context.operations[0].evidence["requestSchema"].verified
