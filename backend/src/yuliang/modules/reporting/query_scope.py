"""与当前IntegrationService相同七阶段/原生关联/权限验证，逐请求重新核对。"""

from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.configuration.integration_models import (
    IntegrationAccount,
    OperationDeclaration,
    VerificationStage,
)
from yuliang.modules.configuration.integration_service import DESCRIPTORS, PAIRS, IntegrationService
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.reporting.query_models import QueryRequest
from yuliang.platform.errors import ApiError, ErrorCode


def query_context(
    session: Session, actor: IntegrationService, value: QueryRequest
) -> tuple[GatewayContext, Any]:
    scope = value.scope
    if (
        scope.tenant_id != str(actor.identity.tenant_id)
        or DESCRIPTORS[scope.product_line][0] != scope.provider
    ):
        raise ApiError(ErrorCode.FORBIDDEN, "企业或产品线范围不匹配")
    sql = PAIRS.replace(
        "WHERE a.provider=:provider",
        """WHERE s.id=:shop AND s.entity_id=:entity AND a.entity_id=:entity
        AND (CAST(:account AS uuid) IS NULL OR a.id=CAST(:account AS uuid))
 AND a.provider=:provider""",
    )
    pairs = (
        session.execute(
            text(sql),
            dict(
                shop=scope.shop_id,
                entity=scope.business_entity_id,
                account=scope.account_id or None,
                provider=scope.provider,
                line=scope.product_line,
            ),
        )
        .mappings()
        .all()
    )
    if not pairs:
        raise ApiError(ErrorCode.FORBIDDEN, "账户、主体或店铺不在当前授权读取范围")
    if len(pairs) != 1:
        raise ApiError(ErrorCode.INVALID_INPUT, "同店铺有多个读取账户，请明确实际账户")
    pair = pairs[0]
    account = IntegrationAccount(
        id=pair["id"],
        native_id=pair["native_id"],
        name=pair["name"],
        business_entity_id=pair["entity_id"],
        provider=scope.provider,
        product_line=scope.product_line,
        authorization="AUTHORIZED"
        if pair["connected_until"] and pair["status"] == "AUTHORIZED"
        else "UNAUTHORIZED",
    )
    if account.authorization != "AUTHORIZED":
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "当前账户缺少有效平台授权")
    now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    head = session.execute(
        text("SELECT catalog_sha256 FROM catalog_head WHERE singleton")
    ).scalar_one_or_none()
    declarations = (
        session.execute(
            text("""SELECT DISTINCT ON(binding_id,resource) *
        FROM integration_operation_assertions WHERE account_id=:account AND resource=:resource
        ORDER BY binding_id,resource,revision DESC"""),
            dict(account=account.id, resource=value.resource),
        )
        .mappings()
        .all()
    )
    candidates = []
    required_stages: tuple[VerificationStage, ...] = (
        "directory",
        "path",
        "applicationScope",
        "account",
        "backend",
        "requestSchema",
    )
    for row in declarations:
        operation, _ = actor._operation(
            session, row, account, scope.product_line, head, now, [scope.shop_id]
        )
        if (
            operation is not None
            and operation.kind == "READ"
            and all(operation.evidence[p].verified for p in required_stages)
        ):
            candidates.append(row)
    if len(candidates) != 1:
        raise ApiError(
            ErrorCode.CAPABILITY_UNAVAILABLE, "该资源缺唯一已核验的只读来源或当前读取权限"
        )
    proof = OperationDeclaration.model_validate(candidates[0]["payload"])
    binding = (
        session.execute(
            text("SELECT * FROM capability_bindings WHERE id=:id AND active"),
            dict(id=proof.binding_id),
        )
        .mappings()
        .one()
    )
    if proof.authorization_id is None:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "读取来源未绑定实际授权")
    return GatewayContext(
        identity=actor.identity,
        shop_id=scope.shop_id,
        account_id=account.id,
        native_account_id=account.native_id,
        connector_id=binding["connector_id"],
        provider=scope.provider,
        product_line=scope.product_line,
        authorization_id=proof.authorization_id,
        request_id=uuid4(),
        binding_id=proof.binding_id,
        capability_version=binding["revision"],
        catalog_id=binding["catalog_id"],
        catalog_sha256=binding["catalog_sha256"],
        authorization_version=proof.authorization_version,
    ), pair
