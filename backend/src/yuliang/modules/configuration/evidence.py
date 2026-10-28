"""字段依据只由维护入口登记；HTTP没有上传枚举或自授可用状态的接口。"""

import hashlib
from uuid import UUID

from sqlalchemy import Connection, text

from yuliang.modules.capability.service import maintenance
from yuliang.modules.configuration.models import PRODUCT_CONTEXTS, FieldProof


def register_field_proof(connection: Connection, tenant_id: int, proof: FieldProof) -> UUID:
    maintenance(connection)
    context = proof.context
    descriptor = PRODUCT_CONTEXTS.get(context.product)
    if descriptor is None:
        raise ValueError("尚无已核对的产品与账户模型，不能录入字段可用依据")
    account = (
        connection.execute(
            text("""SELECT * FROM ad_accounts WHERE tenant_id=:tenant AND id=:account
        AND entity_id=:entity AND provider=:provider AND product_line=:line
        AND deleted_at IS NULL"""),
            {
                "tenant": tenant_id,
                "account": context.account_id,
                "entity": context.business_entity_id,
                "provider": descriptor[0],
                "line": descriptor[1],
            },
        )
        .mappings()
        .first()
    )
    if account is None:
        raise ValueError("字段上下文与实际主体/账户/产品线不一致")
    if (
        context.shop_id
        and connection.execute(
            text(
                "SELECT 1 FROM entity_links WHERE tenant_id=:tenant "
                "AND account_id=:account AND shop_id=:shop"
            ),
            {"tenant": tenant_id, "account": context.account_id, "shop": UUID(context.shop_id)},
        ).first()
        is None
    ):
        raise ValueError("未建立实际店铺/账户关联")
    if proof.checked_at > connection.execute(text("SELECT clock_timestamp()")).scalar_one():
        raise ValueError("字段依据不能来自未来")
    if (
        proof.binding_id is not None
        and connection.execute(
            text("""SELECT 1 FROM capability_bindings WHERE tenant_id=:tenant AND id=:id
        AND account_id=:account AND connector_id=:connector AND revision=:revision
        AND provider=:provider AND product_line=:line"""),
            {
                "tenant": tenant_id,
                "id": proof.binding_id,
                "account": context.account_id,
                "connector": descriptor[2],
                "revision": proof.binding_version,
                "provider": descriptor[0],
                "line": descriptor[1],
            },
        ).first()
        is None
    ):
        raise ValueError("字段依据绑定了其他账户或产品")
    if proof.capability.unit in {"CNY_MINOR", "BEAN"} and proof.capability.unit != (
        "BEAN" if context.product == "beans" else "CNY_MINOR"
    ):
        raise ValueError("字段货币单位与产品不一致")
    if context.product == "beans" and proof.capability.unit == "RATIO":
        raise ValueError("微信豆字段不接受未经核验的ROI参数")
    if proof.authorization_id is not None:
        grant = (
            connection.execute(
                text("""SELECT p.* FROM provider_authorizations p
            WHERE p.tenant_id=:tenant AND p.id=:id AND p.provider=:provider
            AND p.product_line=:line AND (p.account_id=:account OR EXISTS(
              SELECT 1 FROM authorization_account_links l WHERE l.tenant_id=p.tenant_id
              AND l.authorization_id=p.id AND l.account_id=:account))"""),
                {
                    "tenant": tenant_id,
                    "id": proof.authorization_id,
                    "provider": descriptor[0],
                    "line": descriptor[1],
                    "account": context.account_id,
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
            raise ValueError("字段依据的实际授权、版本、scope或账户关联不一致")
    # 锁完整上下文/字段，保留旧修订；不锁粗账户来复用另一目标/版位的字段。
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760307))"),
        {"key": f"{tenant_id}|{context.context_key}|{proof.field_id}"},
    )
    previous = connection.execute(
        text("SELECT tenant_id,payload FROM configuration_field_assertions WHERE id=:id"),
        {"id": proof.id},
    ).first()
    if previous is not None:
        if previous.tenant_id != tenant_id or previous.payload != proof.model_dump(
            mode="json", by_alias=True
        ):
            raise ValueError("同一字段依据ID不能覆盖内容或租户")
        return proof.id
    digest = hashlib.sha256(context.context_key.encode()).hexdigest()
    revision = connection.execute(
        text("""SELECT coalesce(max(revision),0)+1 FROM configuration_field_assertions
        WHERE tenant_id=:tenant AND account_id=:account
        AND context_digest=:digest AND field_id=:field"""),
        {
            "tenant": tenant_id,
            "account": context.account_id,
            "digest": digest,
            "field": proof.field_id,
        },
    ).scalar_one()
    connection.execute(
        text("""INSERT INTO configuration_field_assertions(
        id,tenant_id,account_id,business_entity_id,
        shop_id,context_key,context_digest,field_id,revision,binding_id,authorization_id,
        payload,checked_at,expires_at)
        VALUES(:id,:tenant,:account,:entity,:shop,:context,:digest,:field,:revision,:binding,
          :authorization,CAST(:payload AS jsonb),:checked,:expires)"""),
        {
            "id": proof.id,
            "tenant": tenant_id,
            "account": context.account_id,
            "entity": context.business_entity_id,
            "shop": UUID(context.shop_id) if context.shop_id else None,
            "context": context.context_key,
            "digest": digest,
            "field": proof.field_id,
            "revision": revision,
            "binding": proof.binding_id,
            "authorization": proof.authorization_id,
            "payload": proof.model_dump_json(by_alias=True),
            "checked": proof.checked_at,
            "expires": proof.expires_at,
        },
    )
    return proof.id
