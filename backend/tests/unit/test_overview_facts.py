"""Frozen account-day aggregation and weighted ratios, without source fabrication."""

from datetime import date

from yuliang.modules.reporting.overview_calculator import ai_counts, totals


def test_ai_only_real_matched_completion_not_acceptance_or_mock() -> None:
    jobs = [
        dict(status="SUCCEEDED", execution_receipt=dict(source="PLATFORM", verification=v))
        for v in ("MATCHED", "PENDING", "UNKNOWN")
    ]
    jobs += [
        dict(status="SUCCEEDED", execution_receipt=dict(source="MOCK", verification="MATCHED")),
        dict(status="FAILED"),
        dict(status="PENDING_APPROVAL"),
        dict(status="VERIFYING"),
    ]
    assert ai_counts(jobs) == dict(completed=1, running=1, exceptions=1, pending=1, successRate=0.5)


def fact(account: str, day: date, metric: str, value: int, **changes: object) -> dict:
    return (
        dict(
            account_id=account,
            fact_date=day,
            metric_name=metric,
            value_num=value,
            attribution_window="CLICK_7D/REQUEST_TIME",
            refund_policy="GROSS",
            timezone="Asia/Shanghai",
            unit="MINOR" if metric.endswith("_minor") else "COUNT",
            currency="CNY" if metric.endswith("_minor") else None,
        )
        | changes
    )


def test_totals_weighted_roi_cpm_and_missing_orders() -> None:
    day = date(2026, 10, 1)
    rows = [
        fact("a", day, "spend_minor", 100),
        fact("a", day, "paid_gmv_minor", 1000),
        fact("a", day, "impressions", 100),
        fact("b", day, "spend_minor", 900),
        fact("b", day, "paid_gmv_minor", 900),
        fact("b", day, "impressions", 900),
    ]
    result = totals(rows, {"a", "b"}, (day,))
    assert result.spend == 1000 and result.gmv == 1900 and result.roi == 1.9
    assert result.cpm == 1000 and result.orders is None and result.conversion is None


def test_absent_account_day_does_not_become_zero() -> None:
    day = date(2026, 10, 1)
    assert totals([fact("a", day, "spend_minor", 0)], {"a"}, (day,)).spend == 0
    assert totals([fact("a", day, "spend_minor", 0)], {"a", "b"}, (day,)).spend is None


def test_cross_semantics_and_currency_not_summed() -> None:
    day = date(2026, 10, 1)
    rows = [
        fact("a", day, "spend_minor", 1),
        fact("a", day, "paid_gmv_minor", 2),
        fact("b", day, "spend_minor", 1),
        fact("b", day, "paid_gmv_minor", 3, refund_policy="NET"),
    ]
    assert totals(rows, {"a", "b"}, (day,)).gmv is None
    assert totals([fact("a", day, "spend_minor", 1, currency="USD")], {"a"}, (day,)).spend is None
