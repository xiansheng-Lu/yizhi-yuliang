"""V9实际资源查询：规范日事实与最小商品观察，不调用平台或生成写入口。"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Engine, text

from yuliang.modules.commerce.shop_details import product_view
from yuliang.modules.configuration.integration_service import IntegrationService
from yuliang.modules.reporting.query_facts import query_facts
from yuliang.modules.reporting.query_models import QueryRequest, ResourcePage, ResourceRow
from yuliang.modules.reporting.query_plans import query_adgroups
from yuliang.modules.reporting.query_scope import query_context
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

UNKNOWN_TIME = "源更新时间未提供"


def normalized(value: Any, unit: str, warnings: list[str]) -> int | None:
    if value is None:
        return None
    number = Decimal(str(value))
    if (
        not number.is_finite()
        or number < 0
        or number > 9007199254740991
        or number != number.to_integral_value()
    ):
        warnings.append("UNSAFE_OR_INVALID_" + unit)
        return None
    return int(number)


class QueryService:
    def __init__(self, engine: Engine, identity: VerifiedIdentity):
        self.engine, self.identity = engine, identity

    def query(self, value: QueryRequest) -> ResourcePage:
        if value.filters.cursor:
            raise ApiError(
                ErrorCode.INVALID_INPUT, "本只读切片不支持游标；完整范围过大时明确拒绝，请收窄筛选"
            )
        if (value.scope.product_line, value.resource) not in {
            ("adq", "reports"),
            ("adq", "adgroups"),
            ("wxshop", "compass"),
            ("wxshop", "products"),
            ("wxshop", "stock"),
        }:
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "此资源读模型尚未实现，不返回伪造空集合"
            )
        engine = self.engine.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        with business_transaction(engine, self.identity) as session:
            ctx, pair = query_context(
                session, IntegrationService(self.engine, self.identity), value
            )
            if value.resource == "adgroups":
                return query_adgroups(session, ctx, value)
            if value.resource in {"reports", "compass"}:
                return self._reports(session, ctx, pair, value)
            return self._products(session, ctx, value)

    def _reports(self, session: Any, ctx: Any, pair: Any, value: QueryRequest) -> ResourcePage:
        if value.filters.native_parent_id not in {None, "", ctx.native_account_id}:
            raise ApiError(ErrorCode.INVALID_INPUT, "报表父级只能是当前真实原生账户")
        binding = session.execute(
            text("SELECT normalized_path FROM capability_bindings WHERE id=:id"),
            dict(id=ctx.binding_id),
        ).scalar_one()
        expected = (
            "/v1.3/daily_reports/get"
            if value.resource == "reports"
            else "/channels/ec/compass/shop/overall/get"
        )
        if binding != expected:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "此资源声明不是已实现的规范日报来源")
        table = "adq_report_pages" if value.resource == "reports" else "shop_compass_observations"
        # 两个固定表名，字段来自实际迁移；没有接收客户端SQL标识。
        stmt = {
            "adq_report_pages": """SELECT DISTINCT ON(p.report_date) p.report_date,
              p.fetched_at AS fetched,p.run_id,p.source_id
              FROM adq_report_pages p JOIN sync_sources s
              ON (s.tenant_id,s.id)=(p.tenant_id,p.source_id)
              JOIN sync_runs r ON (r.tenant_id,r.id)=(p.tenant_id,p.run_id)
              WHERE s.account_id=:account AND s.binding_id=:binding AND p.kind='DAILY'
              AND p.report_date BETWEEN :start AND :end
              ORDER BY p.report_date,r.started_at DESC,p.page_no DESC""",
            "shop_compass_observations": """SELECT DISTINCT ON(p.report_date) p.report_date,
              p.fetched_at AS fetched,p.run_id,p.source_id
              FROM shop_compass_observations p JOIN sync_sources s
              ON (s.tenant_id,s.id)=(p.tenant_id,p.source_id)
              JOIN sync_runs r ON (r.tenant_id,r.id)=(p.tenant_id,p.run_id)
              WHERE s.account_id=:account AND s.binding_id=:binding AND p.shop_id=:shop
              AND p.report_date BETWEEN :start AND :end
              ORDER BY p.report_date,r.started_at DESC,p.page_no DESC""",
        }[table]
        pages = (
            session.execute(
                text(stmt),
                dict(
                    account=ctx.account_id,
                    binding=ctx.binding_id,
                    shop=ctx.shop_id,
                    start=value.scope.start,
                    end=value.scope.end,
                ),
            )
            .mappings()
            .all()
        )
        if not pages:
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "所选范围尚无持久同步观察，不能伪造同步时间或零指标",
            )
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for fact in query_facts(session, ctx, value.scope.start, value.scope.end):
            by_day[fact["fact_date"].isoformat()].append(fact)
        warnings = [
            "ACCOUNT_DAY_PROJECTION: nativeId为实际账户ID，不是平台报表单ID；"
            "version=null，不提供写入"
        ]
        rows = []
        refund = set()
        attribution = set()
        for page in sorted(pages, key=lambda r: r["report_date"]):
            day = page["report_date"].isoformat()
            latest = session.execute(
                text(
                    "SELECT id,state FROM sync_runs WHERE source_id=:source "
                    "ORDER BY started_at DESC,id DESC LIMIT 1"
                ),
                dict(source=page["source_id"]),
            ).one()
            facts = [f for f in by_day[day] if f["sync_run_id"] == page["run_id"]]
            if latest.id != page["run_id"] or latest.state != "SUCCESS":
                facts = []
                warnings.append(day + ":LATEST_SOURCE_RUN_INCOMPLETE; 不把旧值冒充本次已确认值")
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for f in facts:
                groups[f["metric_name"]].append(f)
            mapping = (
                {
                    "spend_minor": "spendMinor",
                    "paid_gmv_minor": "attributedGmvMinor"
                    if value.resource == "reports"
                    else "paidGmvMinor",
                    "impressions": "impressions",
                    "clicks": "clicks",
                }
                if value.resource == "reports"
                else {
                    "paid_gmv_minor": "paidGmvMinor",
                    "paid_order_count": "paidOrders",
                    "refund_gmv_minor": "refundMinor",
                }
            )
            metrics: dict[str, int | float | None] = {}
            accepted = {}
            for native, key in mapping.items():
                items = groups.get(native, [])
                if len(items) != 1:
                    metrics[key] = None
                    warnings.append(day + ":MISSING_OR_AMBIGUOUS_" + key)
                    continue
                fact = items[0]
                if (fact["unit"], fact["currency"]) != (
                    ("COUNT", None)
                    if key in {"impressions", "clicks", "paidOrders"}
                    else ("MINOR", "CNY")
                ):
                    metrics[key] = None
                    warnings.append(day + ":UNCONFIRMED_UNIT_" + key)
                    continue
                metrics[key] = normalized(fact["value_num"], key, warnings)
                accepted[key] = fact
                refund.add(fact["refund_policy"])
                attribution.add(fact["attribution_window"])
            if value.resource == "reports":
                spend, gmv = metrics.get("spendMinor"), metrics.get("attributedGmvMinor")
                left, right = accepted.get("spendMinor"), accepted.get("attributedGmvMinor")
                comparable = (
                    left is not None
                    and right is not None
                    and (left["attribution_window"], left["refund_policy"], left["currency"])
                    == (right["attribution_window"], right["refund_policy"], right["currency"])
                )
                metrics["roi"] = (
                    float((Decimal(gmv) / Decimal(spend)).quantize(Decimal(".000001")))
                    if comparable and spend is not None and spend > 0 and gmv is not None
                    else None
                )
                metrics["paidOrders"] = None
            windows = {f["attribution_window"] for f in accepted.values()}
            times = {f["source_updated_at"] for f in accepted.values()}
            source_time = (
                next(iter(times)).isoformat()
                if len(times) == 1 and None not in times
                else UNKNOWN_TIME
            )
            if source_time == UNKNOWN_TIME:
                warnings.append(
                    day + ":SOURCE_UPDATED_AT_UNKNOWN; updatedAt为明确缺失文本，"
                    "syncedAt才为持久抓取时间"
                )
            row = ResourceRow(
                scope_key=value.scope.key(),
                id=str(uuid5(NAMESPACE_URL, value.scope.key() + "|" + value.resource + "|" + day)),
                native_id=ctx.native_account_id,
                name=pair["name"] + " / " + day,
                status="PARTIAL" if any(v is None for v in metrics.values()) else "KNOWN",
                updated_at=source_time,
                metrics=metrics,
                attribution_window=next(iter(windows)) if len(windows) == 1 else None,
            )
            if (
                not value.filters.search
                or value.filters.search.casefold() in (row.name + " " + row.native_id).casefold()
            ):
                rows.append(row)
        first, last = date.fromisoformat(value.scope.start), date.fromisoformat(value.scope.end)
        observed = {p["report_date"] for p in pages}
        missing = [
            (first + timedelta(days=i)).isoformat()
            for i in range((last - first).days + 1)
            if first + timedelta(days=i) not in observed
        ]
        if missing:
            warnings.append("DATE_COVERAGE_MISSING:" + ",".join(missing[:31]))
        return ResourcePage(
            scope_key=value.scope.key(),
            resource=value.resource,
            rows=rows,
            synced_at=max(p["fetched"] for p in pages).isoformat(),
            granularity="ACCOUNT_DAY",
            attribution_window=next(iter(attribution)) if len(attribution) == 1 else None,
            refund_handling=next(iter(refund))
            if len(refund) == 1
            else "UNCONFIRMED_OR_MIXED_NOT_AGGREGATED",
            deduplication="PERSISTED_FULL_FACT_KEY; 不累计重复页，不从单日快照造历史",
            warnings=list(dict.fromkeys(warnings)),
        )

    def _products(self, session: Any, ctx: Any, value: QueryRequest) -> ResourcePage:
        path = session.execute(
            text("SELECT normalized_path FROM capability_bindings WHERE id=:id"),
            dict(id=ctx.binding_id),
        ).scalar_one()
        if path != "/channels/ec/product/get":
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "商品ID列表不冒充已获取SKU详情")
        search = (value.filters.search or "").strip()
        if search and not search.isdecimal():
            raise ApiError(ErrorCode.INVALID_INPUT, "商品名称未采集，本切片search仅支持原生ID片段")
        params = dict(
            shop=ctx.shop_id,
            account=ctx.account_id,
            binding=ctx.binding_id,
            parent=value.filters.native_parent_id or None,
            search=search,
        )
        products = (
            session.execute(
                text("""SELECT DISTINCT c.native_id FROM commerce_products c
            WHERE c.shop_id=:shop AND c.account_id=:account AND c.deleted_at IS NULL
            AND (CAST(:parent AS text) IS NULL OR c.native_id=:parent)
            AND (:search='' OR strpos(c.native_id,:search)>0) ORDER BY c.native_id LIMIT 1001"""),
                params,
            )
            .scalars()
            .all()
        )
        if len(products) > 1000:
            raise ApiError(
                ErrorCode.DATA_COVERAGE_INSUFFICIENT, "完整商品范围过大，请收窄筛选，不返回截断集合"
            )
        synced = session.execute(
            text("""SELECT max(p.fetched_at) FROM shop_product_detail_pages p
            JOIN sync_sources s ON (s.tenant_id,s.id)=(p.tenant_id,p.source_id)
            WHERE p.shop_id=:shop AND p.account_id=:account AND s.binding_id=:binding
            AND (CAST(:parent AS text) IS NULL OR p.product_id=:parent)
            AND (:search='' OR strpos(p.product_id,:search)>0)
            AND EXISTS (SELECT 1 FROM commerce_products c WHERE c.native_id=p.product_id
              AND c.shop_id=p.shop_id AND c.account_id=p.account_id AND c.deleted_at IS NULL)"""),
            params,
        ).scalar_one()
        if synced is None:
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "没有实际商品详情观察，未接入不能当空库存或零库存"
            )
        rows = []
        warnings = [
            "商品名称未采集，name为ID标签；未知源时间明确文字显示，syncedAt为持久抓取时间",
            "未配置新鲜度策略的库存仅为观察，不用于执行前置；不提供写版本",
            "CURRENT_OBSERVATION_ONLY: 商品和库存为最新持久观察，不将所选日期冒充历史库存回放",
        ]
        warnings.append(
            "OBSERVED_SUBSET_ONLY: 本地已知ID及详情观察子集，不宣称完整平台目录或平台无商品"
        )
        for native in products:
            observed = session.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM shop_product_detail_pages p "
                    "JOIN sync_sources s ON (s.tenant_id,s.id)=(p.tenant_id,p.source_id) "
                    "WHERE p.shop_id=:shop AND p.account_id=:account "
                    "AND s.binding_id=:binding AND p.product_id=:native)"
                ),
                dict(params, native=native),
            ).scalar_one()
            if not observed:
                warnings.append(native + ":DETAIL_NOT_OBSERVED; 已知商品缺详情，不将缺失当零库存")
                continue
            view = product_view(session, ctx, native)
            skus = view.get("skus", [])
            if value.resource == "products":
                stocks = [s["stock"] for s in skus]
                stock = sum(stocks) if stocks and all(v is not None for v in stocks) else None
                if stock is not None and stock > 9007199254740991:
                    stock = None
                rows.append(
                    ResourceRow(
                        scope_key=value.scope.key(),
                        id=view["internalId"],
                        native_id=native,
                        name="商品 " + native,
                        status=view["state"],
                        updated_at=view.get("sourceAt") or UNKNOWN_TIME,
                        metrics=dict(
                            stock=stock,
                            itemCount=len(skus) if view["skuPresence"] == "PRESENT" else None,
                        ),
                    )
                )
            else:
                for sku in skus:
                    internal = session.execute(
                        text(
                            "SELECT id FROM commerce_skus WHERE product_id=:product "
                            "AND native_id=:native"
                        ),
                        dict(product=view["internalId"], native=sku["skuId"]),
                    ).scalar_one()
                    rows.append(
                        ResourceRow(
                            scope_key=value.scope.key(),
                            id=str(internal),
                            native_id=sku["skuId"],
                            name="SKU " + sku["skuId"],
                            status=view["state"],
                            updated_at=view.get("sourceAt") or UNKNOWN_TIME,
                            metrics=dict(stock=sku["stock"]),
                            related=[dict(type="PRODUCT", id=view["internalId"])],
                        )
                    )
            if view.get("freshness") != "FRESH":
                warnings.append(native + ":" + str(view.get("freshness")))
            if view["skuPresence"] == "MISSING":
                warnings.append(native + ":SKU_NOT_PROVIDED_NOT_ZERO_STOCK")
        if len(rows) > 1000:
            raise ApiError(
                ErrorCode.DATA_COVERAGE_INSUFFICIENT, "完整SKU范围过大，请收窄到商品，不静默截断"
            )
        return ResourcePage(
            scope_key=value.scope.key(),
            resource=value.resource,
            rows=rows,
            synced_at=synced.isoformat(),
            granularity="LATEST_PRODUCT_OBSERVATION"
            if value.resource == "products"
            else "LATEST_SKU_OBSERVATION",
            refund_handling="NOT_APPLICABLE",
            deduplication="NATIVE_PRODUCT_SKU_KEY; 商品与SKU不互相覆盖",
            warnings=list(dict.fromkeys(warnings)),
        )
