"""维护录入明确的资源/目录/账户映射；不从URL后缀或自由动作名推断。"""

from uuid import UUID

from sqlalchemy import Connection, text

from yuliang.modules.capability.service import maintenance
from yuliang.modules.configuration.integration_models import OperationDeclaration


def register_operation(connection: Connection, tenant_id: int, proof: OperationDeclaration) -> UUID:
    maintenance(connection)
    row = (
        connection.execute(
            text("""SELECT b.*,a.entity_id,a.native_id FROM capability_bindings b
      JOIN ad_accounts a ON (a.tenant_id,a.id)=(b.tenant_id,b.account_id)
      WHERE b.tenant_id=:tenant AND b.id=:id AND a.deleted_at IS NULL"""),
            {"tenant": tenant_id, "id": proof.binding_id},
        )
        .mappings()
        .first()
    )
    if row is None or row["entity_id"] is None or row["revision"] != proof.binding_version:
        raise ValueError("资源操作须绑定实际主体、账户与能力版本")
    if row["operation_kind"] == "READ" and proof.editable_fields:
        raise ValueError("只读能力不能声明可编辑字段")
    if proof.checked_at > connection.execute(text("SELECT clock_timestamp()")).scalar_one():
        raise ValueError("资源操作依据不能来自未来")
    if proof.authorization_id is not None:
        grant = (
            connection.execute(
                text("""SELECT p.version,p.scopes FROM provider_authorizations p
          WHERE p.tenant_id=:tenant AND p.id=:id AND p.provider=:provider AND p.product_line=:line
          AND EXISTS(SELECT 1 FROM authorization_account_links l
            WHERE l.tenant_id=p.tenant_id AND l.authorization_id=p.id
             AND l.account_id=:account AND l.native_id_snapshot=:native)"""),
                {
                    "tenant": tenant_id,
                    "id": proof.authorization_id,
                    "provider": row["provider"],
                    "line": row["product_line"],
                    "account": row["account_id"],
                    "native": row["native_id"],
                },
            )
            .mappings()
            .first()
        )
        if (
            grant is None
            or grant["version"] != proof.authorization_version
            or not set(proof.required_scopes) <= set(grant["scopes"])
        ):
            raise ValueError("资源操作的实际授权、版本、scope或账户关联不一致")
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760311))"),
        {"key": f"{tenant_id}|{proof.binding_id}|{proof.resource}"},
    )
    previous = connection.execute(
        text("SELECT tenant_id,payload FROM integration_operation_assertions WHERE id=:id"),
        {"id": proof.id},
    ).first()
    if previous is not None:
        if previous.tenant_id != tenant_id or previous.payload != proof.model_dump(
            mode="json", by_alias=True
        ):
            raise ValueError("同一资源操作依据ID不能替换内容或租户")
        return proof.id
    revision = connection.execute(
        text("""SELECT coalesce(max(revision),0)+1
      FROM integration_operation_assertions WHERE tenant_id=:tenant AND binding_id=:binding
      AND resource=:resource"""),
        {"tenant": tenant_id, "binding": proof.binding_id, "resource": proof.resource},
    ).scalar_one()
    connection.execute(
        text("""INSERT INTO integration_operation_assertions(
      id,tenant_id,binding_id,account_id,business_entity_id,resource,revision,payload,checked_at,expires_at)
      VALUES(:id,:tenant,:binding,:account,:entity,:resource,:revision,
       CAST(:payload AS jsonb),:checked,:expires)"""),
        {
            "id": proof.id,
            "tenant": tenant_id,
            "binding": proof.binding_id,
            "account": row["account_id"],
            "entity": row["entity_id"],
            "resource": proof.resource,
            "revision": revision,
            "payload": proof.model_dump_json(by_alias=True),
            "checked": proof.checked_at,
            "expires": proof.expires_at,
        },
    )
    return proof.id
