"""仅聚合已覆盖事实，Decimal分子分母重算，不制造利润与CPA。"""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from yuliang.modules.analysis.models import AnalysisQuery
from yuliang.modules.analysis.result_models import AnalysisResult

# id/名称/单位/分子/分母；原始基础项不作为跨产品统一GMV。
DEFINITIONS = {
    "ads": (
        ("spend", "广告消耗", "minor", "spend_minor", None),
        ("gmv", "广告归因GMV", "minor", "paid_gmv_minor", None),
        ("roi", "投放ROI", "ratio", "paid_gmv_minor", "spend_minor"),
        ("ctr", "点击率", "rate", "clicks", "impressions"),
        ("cpc", "点击成本", "minor", "spend_minor", "clicks"),
        ("cpa", "成交成本", "minor", "spend_minor", "attributed_order_count"),
    ),
    "shop": (
        ("gmv", "支付GMV", "minor", "paid_gmv_minor", None),
        ("orders", "来源去重支付订单数", "count", "paid_order_count", None),
        ("aov", "成交客单价", "minor", "paid_gmv_minor", "paid_order_count"),
        ("refund", "来源退款GMV", "minor", "refund_gmv_minor", None),
        ("refundRate", "退款率", "rate", "refund_gmv_minor", "paid_gmv_minor"),
    ),
}


def calculate(
    query: AnalysisQuery,
    facts: list[dict[str, Any]],
    task_id: UUID,
    result_id: UUID,
    shop_name: str,
) -> AnalysisResult:
    def values(dimension: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        line = "adq" if dimension == "ads" else "wxshop"
        required = {
            x
            for definition in DEFINITIONS.get(dimension, ())
            for x in (definition[3], definition[4])
            if x is not None
        }
        chosen = [r for r in rows if r["product_line"] == line and r["metric_name"] in required]
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in chosen:
            groups.setdefault(r["metric_name"], []).append(r)
        totals: dict[str, Decimal] = {}
        for metric, items in groups.items():
            signatures = {
                (r["unit"], r["currency"], r["attribution_window"], r["refund_policy"])
                for r in items
            }
            if len(signatures) != 1:
                raise ValueError("INCOMPARABLE_FACTS")
            totals[metric] = sum((Decimal(str(r["value_num"])) for r in items), Decimal(0))
        result: dict[str, Any] = {}
        for key, _, unit, numerator, denominator in DEFINITIONS.get(dimension, ()):
            a, b = totals.get(numerator), totals.get(denominator) if denominator else None
            if denominator and numerator in groups and denominator in groups:
                left, right = groups[numerator][0], groups[denominator][0]
                if (left["attribution_window"], left["refund_policy"]) != (
                    right["attribution_window"],
                    right["refund_policy"],
                ):
                    a = None
            if (
                dimension == "shop"
                and denominator
                and any(r["refund_policy"] != "GROSS" for r in chosen)
            ):
                a = None
            value = (
                a
                if denominator is None
                else (a / b if a is not None and b is not None and b > 0 else None)
            )
            if value is not None:
                if unit in {"minor", "count"} and denominator is None:
                    value = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if abs(value) > 9007199254740991:
                    raise ValueError("UNSAFE_NUMERIC")
                result[dimension + "." + key] = (
                    int(value)
                    if unit in {"minor", "count"} and denominator is None
                    else float(value.quantize(Decimal(".000001")))
                )
            else:
                result[dimension + "." + key] = None
        if dimension == "ads":
            for field, raw_field in (
                ("clicks", "clicks"),
                ("impressions", "impressions"),
                ("orders", "attributed_order_count"),
            ):
                raw = totals.get(raw_field)
                if raw is not None and abs(raw) > 9007199254740991:
                    raise ValueError("UNSAFE_NUMERIC")
                result["ads." + field] = int(raw) if raw is not None else None
        if dimension == "shop":
            result["shop.grossGmvUnavailable"] = None
        return result

    metrics = []
    for dimension in query.dimensions:
        computed = values(dimension, facts)
        for key, name, unit, num, den in DEFINITIONS.get(dimension, ()):
            value = computed[dimension + "." + key]
            if (
                dimension == "shop"
                and key == "gmv"
                and any(
                    r["product_line"] == "wxshop" and r["refund_policy"] != "GROSS" for r in facts
                )
            ):
                name = "来源退款处理后成交金额"
            base_keys = {
                "spend_minor": "spend",
                "paid_gmv_minor": "gmv",
                "paid_order_count": "orders",
                "attributed_order_count": "orders",
                "refund_gmv_minor": "refund",
                "clicks": "clicks",
                "impressions": "impressions",
            }
            formula = {}
            if den is not None:
                formula = dict(
                    numerator=dimension + "." + base_keys[num],
                    denominator=dimension + "." + base_keys[den],
                )
                non_gross = dimension == "shop" and any(
                    r["product_line"] == "wxshop" and r["refund_policy"] != "GROSS" for r in facts
                )
                if non_gross:
                    formula["numerator" if key == "aov" else "denominator"] = (
                        "shop.grossGmvUnavailable"
                    )
            metrics.append(
                dict(
                    **formula,
                    id=dimension + "." + key,
                    dimension=dimension,
                    name=name,
                    unit=unit,
                    definition=f"规范{num}" + (f" ÷ 同范围{den}" if den else "同口径合计"),
                    value=value,
                    previous=None,
                    yearAgo=None,
                    status="ready" if value is not None else "empty",
                    reason=None if value is not None else "缺少明确分子/分母或分母非正，未补零",
                )
            )
    points = []
    for day in sorted({r["fact_date"] for r in facts}):
        row = {}
        for dimension in query.dimensions:
            row.update(values(dimension, [r for r in facts if r["fact_date"] == day]))
        points.append(dict(date=day, values=row))
    sections = [
        dict(
            id=d,
            dimension=d,
            title="投流分析" if d == "ads" else "店铺经营分析",
            topics=["已确认来源汇总"],
            metricIds=[m["id"] for m in metrics if m["dimension"] == d],
            rows=[],
            conclusion="已按所选周期核对规范事实。",
            advice="核对原始来源后制定后续行动。",
            limitation="账户级数据，不制造计划/商品排名；比较期计算尚未实现，暂不计算同比环比。",
        )
        for d in query.dimensions
    ]
    return AnalysisResult(
        id=result_id,
        task_id=task_id,
        query=query,
        shop_name=shop_name,
        platform_name=query.platform_id,
        created_at=datetime.now(UTC).isoformat(),
        completeness="partial",
        coverage="本期按实际日期、账户及规范合同完整覆盖；比较期计算尚未实现",
        attribution="产品线独立计算；归因/退款/币种口径来自冻结事实",
        comparison=dict(previous=None, yearAgo=None),
        metrics=metrics,
        points=points,
        sections=sections,
        rules=dict(
            declinePercent=20, spendRisePercent=30, roiFloor=3, minSpendMinor=100000, minViews=1000
        ),
    )
