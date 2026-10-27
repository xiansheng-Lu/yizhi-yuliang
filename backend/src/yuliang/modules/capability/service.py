"""读取聚合不授予权限；证据/许可录入仅限维护身份，无自授权HTTP入口。"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.capability.models import (
    Binding,
    Evidence,
    ValidationPermit,
    aggregate,
    proof_matches,
)
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode


def maintenance(connection: Connection) -> None:
    if connection.execute(text("SELECT current_user")).scalar_one() != "app_owner":
        raise ValueError("能力证据与许可录入需要独立维护身份")


def register_binding(connection: Connection, binding: Binding) -> None:
    maintenance(connection)
    source = (
        connection.execute(
            text(
                "SELECT normalized_path,http_method FROM endpoint_catalog "
                "WHERE catalog_sha256=:sha AND catalog_id=:id AND provider=:provider"
            ),
            {"sha": binding.catalog_sha256, "id": binding.catalog_id, "provider": binding.provider},
        )
        .mappings()
        .one()
    )
    if (source["normalized_path"] and source["normalized_path"] != binding.normalized_path) or (
        source["http_method"] != "UNKNOWN" and source["http_method"] != binding.http_method
    ):
        raise ValueError("能力绑定与所引用目录的已知方法或路径不一致")
    connection.execute(
        text("""
      INSERT INTO capability_bindings(id,tenant_id,account_id,connector_id,provider,product_line,
       action,operation_kind,catalog_sha256,catalog_id,revision,http_method,normalized_path,permission_code)
      VALUES(:id,:tenant_id,:account_id,:connector_id,:provider,:product_line,:action,:operation_kind,
       :catalog_sha256,:catalog_id,:revision,:http_method,:normalized_path,:permission_code)
    """),
        binding.model_dump(),
    )


def record_evidence(connection: Connection, binding_id: UUID, evidence: Evidence) -> Evidence:
    maintenance(connection)
    row = (
        connection.execute(
            text("SELECT * FROM capability_bindings WHERE id=:id AND active FOR UPDATE"),
            {"id": binding_id},
        )
        .mappings()
        .one()
    )
    binding = Binding.model_validate({key: row[key] for key in Binding.model_fields})
    if not proof_matches(binding, evidence) or evidence.checked_at > datetime.now(UTC):
        raise ValueError("证据类型或时间不符合对应读写上下文")
    existing = (
        connection.execute(
            text("SELECT * FROM capability_assertions WHERE id=:id"), {"id": evidence.id}
        )
        .mappings()
        .first()
    )
    if existing:
        stored = Evidence.model_validate({key: existing[key] for key in Evidence.model_fields})
        if existing["binding_id"] != binding_id or stored.model_copy(
            update={"sequence": 0}
        ) != evidence.model_copy(update={"sequence": 0}):
            raise ValueError("同一证据ID不能覆盖内容或上下文")
        return stored
    sequence = connection.execute(
        text("""
      INSERT INTO capability_assertions(id,tenant_id,binding_id,phase,result,proof_kind,
        proof_ref,checked_by,checked_at,expires_at)
      VALUES(:id,:tenant,:binding,:phase,:result,:proof_kind,:proof_ref,:checked_by,:checked_at,:expires_at)
      RETURNING sequence
    """),
        evidence.model_dump(exclude={"sequence"})
        | {"tenant": binding.tenant_id, "binding": binding_id},
    ).scalar_one()
    return evidence.model_copy(update={"sequence": sequence})


def register_validation_permit(connection: Connection, permit: ValidationPermit) -> None:
    maintenance(connection)
    binding = (
        connection.execute(
            text(
                "SELECT tenant_id,operation_kind,provider FROM capability_bindings "
                "WHERE id=:id AND active FOR UPDATE"
            ),
            {"id": permit.binding_id},
        )
        .mappings()
        .one()
    )
    if binding["operation_kind"] != "WRITE" or binding["provider"] != "tencent":
        raise ValueError("本轮验证许可仅适用于腾讯写操作")
    connection.execute(
        text("""
      INSERT INTO capability_validation_permits(id,tenant_id,binding_id,requester_id,approved_by,
        approval_ref,request_key,request_digest,max_amount_minor,starts_at,expires_at)
      VALUES(:id,:tenant,:binding_id,:requester_id,:approved_by,:approval_ref,
        :request_key,:request_digest,:max_amount_minor,:starts_at,:expires_at)
    """),
        permit.model_dump() | {"tenant": binding["tenant_id"]},
    )


def evaluate_binding(
    session: Session, identity: VerifiedIdentity, shop_id: UUID, binding_id: UUID
) -> dict[str, Any]:
    row = (
        session.execute(
            text("SELECT * FROM capability_bindings WHERE id=:id AND active"), {"id": binding_id}
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(ErrorCode.NO_DATA, "没有当前有效的能力上下文")
    binding = Binding.model_validate({key: row[key] for key in Binding.model_fields})
    try:
        require_shop_account(
            session, identity, shop_id, binding.account_id, binding.permission_code
        )
    except ApiError as error:
        if error.code != ErrorCode.FORBIDDEN:
            raise
        return aggregate(binding, [], now=datetime.now(UTC), permitted=False)
    current = session.execute(
        text("SELECT catalog_sha256 FROM catalog_head WHERE singleton")
    ).scalar_one_or_none()
    if current != binding.catalog_sha256:
        return aggregate(binding, [], now=datetime.now(UTC)) | {
            "reason": "目录版本已变化，需重新绑定和取证"
        }
    account = session.execute(
        text("SELECT status FROM ad_accounts WHERE id=:id AND deleted_at IS NULL"),
        {"id": binding.account_id},
    ).scalar_one_or_none()
    rows = session.execute(
        text("SELECT * FROM capability_assertions WHERE binding_id=:id"), {"id": binding.id}
    ).mappings()
    evidence = [
        Evidence.model_validate({key: item[key] for key in Evidence.model_fields}) for item in rows
    ]
    result = aggregate(
        binding, evidence, now=datetime.now(UTC), account_authorized=account == "AUTHORIZED"
    )
    if (
        result["validationReady"]
        and session.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM capability_validation_permits "
                "WHERE binding_id=:id AND used_at IS NOT NULL)"
            ),
            {"id": binding.id},
        ).scalar_one()
    ):
        result.update(validationReady=False, reason="首次验证许可已领取，须核对原请求结果")
    return result


def evaluate_action(
    session: Session,
    identity: VerifiedIdentity,
    shop_id: UUID,
    account_id: UUID,
    connector: str,
    product_line: str,
    action: str,
) -> dict[str, Any]:
    ids = (
        session.execute(
            text("""
      SELECT id FROM capability_bindings WHERE account_id=:account AND connector_id=:connector
       AND product_line=:product AND action=:action AND active ORDER BY id
    """),
            {
                "account": account_id,
                "connector": connector,
                "product": product_line,
                "action": action,
            },
        )
        .scalars()
        .all()
    )
    results = [evaluate_binding(session, identity, shop_id, value) for value in ids]
    priority = {
        "ERROR": 0,
        "FORBIDDEN": 1,
        "UNAUTHORIZED": 2,
        "NOT_INTEGRATED": 3,
        "NOT_APPLICABLE": 4,
        "AUTHORIZED": 5,
    }
    status = (
        min((str(item["status"]) for item in results), key=lambda key: priority[key])
        if results
        else "NOT_INTEGRATED"
    )
    return {
        "status": status,
        "operations": [operation for item in results for operation in item["operations"]]
        if status == "AUTHORIZED"
        else [],
        "bindings": results,
    }


def consume_validation(
    session: Session,
    permit_id: UUID,
    shop_id: UUID,
    request_key: UUID,
    request_digest: str,
    amount_minor: int,
) -> bool:
    if type(amount_minor) is not int or not 0 <= amount_minor <= 9007199254740991:
        return False
    return (
        session.execute(
            text("SELECT access.consume_capability_validation(:permit,:shop,:key,:digest,:amount)"),
            {
                "permit": permit_id,
                "shop": shop_id,
                "key": request_key,
                "digest": request_digest,
                "amount": amount_minor,
            },
        ).scalar_one()
        is True
    )
