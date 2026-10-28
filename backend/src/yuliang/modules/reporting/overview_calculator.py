"""Whole frozen account-day ranges, integer amounts and weighted ratios."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from yuliang.modules.reporting.overview_models import OverviewTotals
from yuliang.modules.reporting.query_service import normalized


def ai_counts(jobs: list[dict[str, Any]]) -> dict[str, int | float | None]:
    completed = sum(
        j["status"] == "SUCCEEDED"
        and (j.get("execution_receipt") or {}).get("source") == "PLATFORM"
        and (j.get("execution_receipt") or {}).get("verification") == "MATCHED"
        for j in jobs
    )
    failed = sum(j["status"] == "FAILED" for j in jobs)
    return dict(
        completed=completed,
        running=sum(j["status"] in {"EXECUTING", "VERIFYING"} for j in jobs),
        exceptions=sum(j["status"] in {"FAILED", "BLOCKED", "MANUAL_REQUIRED"} for j in jobs),
        pending=sum(j["status"] == "PENDING_APPROVAL" for j in jobs),
        successRate=completed / (completed + failed) if completed + failed else None,
    )


def days(start: str, end: str) -> tuple[date, ...]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return tuple(first + timedelta(days=i) for i in range((last - first).days + 1))


def semantics(facts: list[dict[str, Any]]) -> set[tuple[Any, ...]]:
    return {
        (f["attribution_window"], f["refund_policy"], f["timezone"])
        for f in facts
        if f["metric_name"] in {"spend_minor", "paid_gmv_minor"}
    }


def totals(
    facts: list[dict[str, Any]], accounts: set[str], dates: tuple[date, ...]
) -> OverviewTotals:
    index: dict[tuple[str, date, str], list[dict[str, Any]]] = {}
    for f in facts:
        index.setdefault((str(f["account_id"]), f["fact_date"], f["metric_name"]), []).append(f)
    values: dict[str, int | None] = {}
    for metric in ("spend_minor", "paid_gmv_minor", "impressions", "clicks"):
        total = 0
        valid = True
        for account in accounts:
            for day in dates:
                rows = index.get((account, day, metric), [])
                if len(rows) != 1:
                    valid = False
                    continue
                row = rows[0]
                money = metric.endswith("_minor")
                if (row["unit"], row["currency"]) != (
                    ("MINOR", "CNY") if money else ("COUNT", None)
                ):
                    valid = False
                    continue
                value = normalized(row["value_num"], metric, [])
                if value is None:
                    valid = False
                else:
                    total += value
        values[metric] = normalized(total, metric, []) if valid else None
    spend, gmv = values["spend_minor"], values["paid_gmv_minor"]
    if len(semantics(facts)) != 1:
        gmv = None
    impressions = values["impressions"]
    return OverviewTotals(
        spend=spend,
        gmv=gmv,
        roi=float(Decimal(gmv) / Decimal(spend)) if gmv is not None and spend else None,
        cpm=float(Decimal(spend) * 1000 / Decimal(impressions))
        if spend is not None and impressions
        else None,
    )
