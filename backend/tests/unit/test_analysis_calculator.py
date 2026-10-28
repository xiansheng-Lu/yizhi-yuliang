"""实际总分子分母、口径冲突和缺失不补0。"""

from typing import Any
from uuid import uuid4

import pytest

from yuliang.modules.analysis.calculator import calculate
from yuliang.modules.analysis.models import AnalysisQuery


def facts() -> list[dict[str, Any]]:
    rows = []
    for day, cost, gmv in [("2026-10-01", "100", "1000"), ("2026-10-02", "900", "900")]:
        for metric, value in [
            ("spend_minor", cost),
            ("paid_gmv_minor", gmv),
            ("clicks", "10"),
            ("impressions", "100"),
        ]:
            rows.append(
                dict(
                    fact_date=day,
                    product_line="adq",
                    metric_name=metric,
                    value_num=value,
                    unit="COUNT" if metric in {"clicks", "impressions"} else "MINOR",
                    currency=None if metric in {"clicks", "impressions"} else "CNY",
                    attribution_window="7d",
                    refund_policy="GROSS",
                )
            )
    return rows


def run(rows: list[dict[str, Any]]) -> Any:
    q = AnalysisQuery(
        shop_id=uuid4(),
        platform_id="adq",
        start_date="2026-10-01",
        end_date="2026-10-07",
        dimensions=("ads",),
        timezone="Asia/Shanghai",
    )
    return calculate(q, rows, uuid4(), uuid4(), "Synthetic")


def test_weighted_ratio_not_daily_average_and_missing_cpa() -> None:
    result = run(facts())
    metrics = {m["id"]: m for m in result.metrics}
    assert metrics["ads.roi"]["value"] == 1.9
    assert metrics["ads.ctr"]["value"] == 0.1
    assert metrics["ads.cpa"]["value"] is None
    assert result.completeness == "partial"


def test_unrelated_raw_ratio_does_not_enter_calculation() -> None:
    rows = facts() + [
        dict(product_line="adq", metric_name="roi", value_num=None, fact_date="2026-10-01")
    ]
    assert {m["id"]: m for m in run(rows).metrics}["ads.roi"]["value"] == 1.9


def test_cross_attribution_refund_or_currency_never_mix() -> None:
    rows = facts()
    rows[-3]["refund_policy"] = "NET_OF_REFUND"
    with pytest.raises(ValueError, match="INCOMPARABLE_FACTS"):
        run(rows)


def test_net_shop_amount_not_gross_or_standard_refund_rate() -> None:
    rows = []
    for metric, value in [
        ("paid_gmv_minor", "800"),
        ("paid_order_count", "2"),
        ("refund_gmv_minor", "200"),
    ]:
        rows.append(
            dict(
                fact_date="2026-10-01",
                product_line="wxshop",
                metric_name=metric,
                value_num=value,
                unit="COUNT" if metric == "paid_order_count" else "MINOR",
                currency=None if metric == "paid_order_count" else "CNY",
                attribution_window="business",
                refund_policy="NET_OF_REFUND",
            )
        )
    q = AnalysisQuery(
        shop_id=uuid4(),
        platform_id="wxshop",
        start_date="2026-10-01",
        end_date="2026-10-01",
        dimensions=("shop",),
        timezone="Asia/Shanghai",
    )
    result = calculate(q, rows, uuid4(), uuid4(), "Synthetic")
    metrics = {m["id"]: m for m in result.metrics}
    assert "退款处理后" in metrics["shop.gmv"]["name"]
    assert metrics["shop.refundRate"]["value"] is None and metrics["shop.aov"]["value"] is None
    assert metrics["shop.orders"]["name"] == "来源去重支付订单数"
