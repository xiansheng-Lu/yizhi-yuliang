"""已证实口径才生成经营事实，经营与广告来源维度独立。"""

from datetime import date
from typing import Any

from yuliang.modules.commerce.compass_contracts import CompassContract
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.reporting.models import SAFE_INTEGER, FactDimensions, MetricSample


def compass_samples(
    ctx: GatewayContext, day: date, values: dict[str, Any], contract: CompassContract | None
) -> tuple[list[MetricSample], list[str]]:
    if contract is None:
        return [], ["COMPASS_CONTRACT_UNCONFIRMED"]
    samples, gaps = [], []
    for field, metric, unit in (
        ("pay_gmv", "paid_gmv_minor", "MINOR"),
        ("pay_order_cnt", "paid_order_count", "COUNT"),
        ("pay_refund_gmv", "refund_gmv_minor", "MINOR"),
    ):
        raw = values.get(field)
        value = int(raw) if raw is not None else None
        if value is not None and unit == "MINOR" and contract.money_unit == "CNY_YUAN_INTEGER":
            value *= 100
        if value is None or not 0 <= value <= SAFE_INTEGER:
            gaps.append(field.upper() + "_MISSING_OR_UNSAFE")
            value = None
        dimensions = FactDimensions.model_validate(
            dict(
                fact_date=day,
                granularity="DAY",
                provider="wxshop",
                product_line="wxshop",
                entity_kind="ACCOUNT",
                native_entity_id=ctx.native_account_id,
                account_id=ctx.account_id,
                shop_id=ctx.shop_id,
                metric_name=metric,
                unit=unit,
                currency="CNY" if unit == "MINOR" else None,
                timezone=contract.timezone,
                attribution_window=contract.definition_key,
                refund_policy=contract.refund_policy,
            )
        )
        samples.append(MetricSample(dimensions=dimensions, value=value))
    return samples, gaps
