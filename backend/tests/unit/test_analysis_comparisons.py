"""对比策略与真实前端等长前期一致，闰年明示。"""

from uuid import uuid4

import pytest
from test_analysis_calculator import facts, run

from yuliang.modules.analysis.comparisons import apply_comparisons, comparison_ranges
from yuliang.modules.analysis.models import AnalysisQuery


def query(start: str, end: str) -> AnalysisQuery:
    return AnalysisQuery(
        shop_id=uuid4(),
        platform_id="adq",
        start_date=start,
        end_date=end,
        dimensions=("ads",),
        timezone="Asia/Shanghai",
    )


@pytest.mark.parametrize(
    "start,end,previous,year_ago",
    [
        ("2026-10-05", "2026-10-11", ("2026-09-28", "2026-10-04"), ("2025-10-05", "2025-10-11")),
        ("2024-02-29", "2024-02-29", ("2024-02-28", "2024-02-28"), ("2023-02-28", "2023-02-28")),
        ("2024-02-01", "2024-02-29", ("2024-01-03", "2024-01-31"), ("2023-02-01", "2023-02-28")),
        ("2025-02-01", "2025-02-28", ("2025-01-04", "2025-01-31"), ("2024-02-01", "2024-02-29")),
        ("2024-02-26", "2024-03-03", ("2024-02-19", "2024-02-25"), ("2023-02-26", "2023-03-04")),
    ],
)
def test_calendar_and_equal_length_windows(
    start: str, end: str, previous: tuple[str, str], year_ago: tuple[str, str]
) -> None:
    windows = comparison_ranges(query(start, end))
    assert tuple(windows["previous"].values()) == previous
    assert tuple(windows["yearAgo"].values()) == year_ago


def test_missing_is_explicit_and_not_not_implemented() -> None:
    current = run(facts())
    windows = comparison_ranges(current.query)
    bundle = {
        k: dict(**v, status="missing", reason="实际缺少一天规范来源", facts=[])
        for k, v in windows.items()
    }
    result = apply_comparisons(current, bundle)
    assert result.comparison == dict(previous=None, yearAgo=None)
    assert "尚未实现" not in result.coverage
    assert all("实际缺少一天" in m["reason"] for m in result.metrics)
