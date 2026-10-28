"""V9广告组当前完整配置观察；无平台调用，不将配置目标当经营效果。"""

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.adq_assets import native_integer
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.delivery.plan_reads import read_plan_observations
from yuliang.modules.reporting.query_adgroup_cursor import MAX_TOTAL, paginate
from yuliang.modules.reporting.query_models import QueryRequest, ResourcePage, ResourceRow
from yuliang.platform.errors import ApiError, ErrorCode

UNKNOWN_TIME = "源更新时间未提供"
MAX_ROWS = MAX_TOTAL


def project_adgroups(
    view: dict[str, Any],
    value: QueryRequest,
    synced_at: str,
    *,
    resolved_account_id: str,
    resolved_tenant_id: int,
) -> ResourcePage:
    """仅映射完整批次；独立量纲证明缺失不阻止有据资源身份显示。"""
    if view["status"] not in {"ready", "empty", "coverage_error"}:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "广告组最新完整读取尚未就绪")
    warnings = [
        "CURRENT_CONFIG_OBSERVATION_ONLY: 所选日期仅为界面范围，不代表历史配置回放",
        "READ_ONLY: version=null，不提供写入或执行许可",
        "TARGET_ROI_NOT_PERFORMANCE_ROI: 不将出价目标或账户报表冒充广告组效果",
        "OBSERVATION_ONLY: 无有效新鲜度/量纲证明的配置不用于执行前置",
    ]
    warnings.extend(view["gaps"])
    measured = {r["nativeId"]: r["values"] for r in view["normalized"]}
    rows = []
    search = (value.filters.search or "").strip().casefold()
    parent = value.filters.native_parent_id or None
    if parent is not None:
        try:
            native_integer(parent, positive=True)
        except ValueError as exc:
            raise ApiError(
                ErrorCode.INVALID_INPUT, "父级须为真实原生计划int64 ID，不是本地UUID"
            ) from exc
    seen = set()
    if len(view["observations"]) > MAX_ROWS:
        raise ApiError(
            ErrorCode.DATA_COVERAGE_INSUFFICIENT, "完整广告组来源超过10000条，不返回截断集合"
        )
    for observation in view["observations"]:
        native, fields = observation["nativeId"], observation["values"]
        if native in seen or fields.get("adgroup_id") != native or fields.get("level") != "ADGROUP":
            raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "广告组身份或层级覆盖不一致")
        seen.add(native)
        if parent is not None and fields.get("campaign_id") != parent:
            continue
        name = fields.get("adgroup_name")
        if not isinstance(name, str) or not name.strip():
            raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "真实广告组缺名称，不能补造标签")
        if search and search not in (name + " " + native).casefold():
            continue
        normalized = measured.get(native)
        status = fields.get("configured_status")
        if fields.get("is_deleted") is True:
            status = "DELETED"
        elif fields.get("is_deleted") is not False:
            status = "UNKNOWN"
            warnings.append("DELETION_STATE_UNCONFIRMED")
        elif normalized is not None:
            status = normalized["status"]
        else:
            warnings.append("CONFIGURED_STATUS_IS_RAW_PLATFORM_ENUM")
        if not isinstance(status, str) or not status:
            status = "UNKNOWN"
            warnings.append("CONFIGURED_STATUS_MISSING")
        rows.append(
            ResourceRow(
                scope_key=value.scope.key(),
                id=str(
                    uuid5(
                        NAMESPACE_URL,
                        f"{resolved_tenant_id}|adq|{resolved_account_id}|ADGROUP|{native}",
                    )
                ),
                native_id=native,
                name=name,
                status=status,
                updated_at=normalized["updatedAt"] if normalized else UNKNOWN_TIME,
                metrics={"budgetMinor": normalized["budgetMinor"] if normalized else None},
                money_type="AD_BUDGET" if normalized else None,
            )
        )
    try:
        rows.sort(key=lambda row: int(native_integer(row.native_id, positive=True)))
    except ValueError:
        raise ApiError(
            ErrorCode.DATA_COVERAGE_INSUFFICIENT, "广告组原生ID非法，无法稳定排序"
        ) from None
    if any(r.updated_at == UNKNOWN_TIME for r in rows):
        warnings.append("SOURCE_UPDATED_AT_UNKNOWN: syncedAt为持久抓取时间，不是源更新时间")
    return ResourcePage(
        scope_key=value.scope.key(),
        resource="adgroups",
        rows=rows,
        synced_at=synced_at,
        granularity="LATEST_ADGROUP_CONFIG_OBSERVATION",
        refund_handling="NOT_APPLICABLE_CONFIG_NOT_PERFORMANCE",
        deduplication="COMPLETE_RUN_NATIVE_ADGROUP_KEY; 不累计重复页或混合不同批次",
        warnings=list(dict.fromkeys(warnings)),
    )


def query_adgroups(session: Session, ctx: GatewayContext, value: QueryRequest) -> ResourcePage:
    binding = session.execute(
        text("SELECT normalized_path,http_method FROM capability_bindings WHERE id=:id"),
        dict(id=ctx.binding_id),
    ).one()
    if (binding.normalized_path, binding.http_method) != ("/v1.3/adgroups/get", "GET"):
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "不是已实现的正式广告组只读协议")
    assertion = (
        session.execute(
            text("""SELECT payload,revision FROM integration_operation_assertions
        WHERE binding_id=:binding AND resource='adgroups' ORDER BY revision DESC LIMIT 1"""),
            dict(binding=ctx.binding_id),
        )
        .mappings()
        .one()
    )
    declaration = assertion["payload"]
    application = session.execute(
        text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
        dict(id=ctx.authorization_id),
    ).scalar_one()
    view = read_plan_observations(
        session, ctx, "ADGROUP", application, frozenset(declaration["requiredScopes"])
    )
    if view["status"] not in {"ready", "empty", "coverage_error"}:
        raise ApiError(
            ErrorCode.VERSION_CONFLICT
            if value.filters.cursor
            else ErrorCode.CAPABILITY_UNAVAILABLE,
            "广告组最新完整读取已改变或未就绪，请重新查询",
        )
    snapshot = view["snapshot"]
    if (
        not isinstance(snapshot, dict)
        or not snapshot.get("complete")
        or not snapshot.get("fetchedAt")
    ):
        raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "完整广告组批次缺持久抓取时间")
    page = project_adgroups(
        view,
        value,
        snapshot["fetchedAt"],
        resolved_account_id=str(ctx.account_id),
        resolved_tenant_id=ctx.identity.tenant_id,
    )
    return paginate(session, ctx, value, page, snapshot | dict(assertion=dict(assertion)))
