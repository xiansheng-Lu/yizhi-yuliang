"""比较窗口及覆盖/跨期口径预检；只使用任务原账户范围。"""

import calendar
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from yuliang.modules.analysis.models import AnalysisQuery
from yuliang.modules.reporting.coverage import require_coverage
from yuliang.platform.errors import ApiError, ErrorCode

POLICY = (
    "previous=等长紧邻前期；yearAgo=去年起日夹至有效日，日/周保持等长，"
    "完整自然月按去年同月；不做日均归一化"
)


def comparison_ranges(query: AnalysisQuery) -> dict[str, dict[str, str]]:
    start, end = date.fromisoformat(query.start_date), date.fromisoformat(query.end_date)
    length = (end - start).days + 1
    year_start = date(
        start.year - 1,
        start.month,
        min(start.day, calendar.monthrange(start.year - 1, start.month)[1]),
    )
    whole_month = (
        start.day == 1
        and start.year == end.year
        and start.month == end.month
        and end.day == calendar.monthrange(end.year, end.month)[1]
    )
    year_end = (
        date(
            year_start.year,
            year_start.month,
            calendar.monthrange(year_start.year, year_start.month)[1],
        )
        if whole_month
        else year_start + timedelta(days=length - 1)
    )
    return dict(
        previous=dict(
            startDate=(start - timedelta(days=length)).isoformat(),
            endDate=(start - timedelta(days=1)).isoformat(),
        ),
        yearAgo=dict(startDate=year_start.isoformat(), endDate=year_end.isoformat()),
    )


def comparison_preflight(
    session: Session, query: AnalysisQuery, accounts: tuple[UUID, ...]
) -> dict[str, Any]:
    current = require_coverage(session, query, account_scope=accounts)
    result: dict[str, Any] = {}
    for name, window in comparison_ranges(query).items():
        comparison = query.model_copy(
            update=dict(start_date=window["startDate"], end_date=window["endDate"])
        )
        try:
            covered = require_coverage(
                session, comparison, account_scope=accounts, comparison_window=True
            )
            if covered["signatures"] != current["signatures"]:
                result[name] = dict(
                    status="incomparable", reason="跨期单位、币种、归因或退款口径不可比", **window
                )
            else:
                result[name] = dict(status="complete", reason=None, **window)
        except ApiError as error:
            if error.code != ErrorCode.DATA_COVERAGE_INSUFFICIENT:
                raise
            reason = "；".join(d["message"] for d in error.details[:3])
            result[name] = dict(
                status="missing", reason=reason or "比较期实际规范来源覆盖不足", **window
            )
    return result


def apply_comparisons(result: Any, bundle: dict[str, Any]) -> Any:
    from yuliang.modules.analysis.calculator import calculate

    metrics = [dict(m) for m in result.metrics]
    ranges: dict[str, Any] = {}
    points = {}
    notes = []
    for name, field in (("previous", "previous"), ("yearAgo", "yearAgo")):
        entry = bundle.get(name, {})
        if entry.get("status") == "complete":
            query = result.query.model_copy(
                update=dict(start_date=entry["startDate"], end_date=entry["endDate"])
            )
            computed = calculate(query, entry["facts"], result.task_id, result.id, result.shop_name)
            by_id = {m["id"]: m for m in computed.metrics}
            for metric in metrics:
                compared = by_id.get(metric["id"], {})
                metric[field] = compared.get("value")
                if metric[field] is None:
                    label = "前期" if name == "previous" else "去年同期"
                    reason = compared.get("reason") or "该指标缺少可计算的比较基数"
                    metric["reason"] = "；".join(
                        x for x in (metric.get("reason"), label + ":" + reason) if x
                    )
            ranges[name] = dict(startDate=entry["startDate"], endDate=entry["endDate"])
            points[name] = computed.points
        else:
            reason = entry.get("reason") or "旧执行快照未冻结比较期，需新任务重新分析"
            notes.append(name + ":" + reason)
            ranges[name] = None
            points[name] = []
            for metric in metrics:
                metric[field] = None
                label = "前期" if name == "previous" else "去年同期"
                metric["reason"] = "；".join(
                    x for x in (metric.get("reason"), label + ":" + reason) if x
                )
    complete = not notes and all(m["status"] == "ready" for m in metrics)
    sections = [
        dict(
            s,
            limitation="账户级数据，不制造计划/商品排名；"
            + ("；".join(notes) if notes else "比较期同口径来源完整")
            + "；"
            + POLICY,
        )
        for s in result.sections
    ]
    return result.model_copy(
        update=dict(
            metrics=metrics,
            comparison=ranges,
            previous_points=points["previous"],
            year_ago_points=points["yearAgo"],
            sections=sections,
            completeness="complete" if complete else "partial",
            coverage="本期及比较期已按原账户范围实际核对；"
            + ("；".join(notes) if notes else "比较来源完整")
            + "；"
            + POLICY,
        )
    )
