"""北京时间自然周期，无补零或缩期降级。"""

from uuid import uuid4

import pytest

from yuliang.modules.analysis.coverage import period_days
from yuliang.modules.analysis.models import AnalysisQuery
from yuliang.platform.errors import ApiError


@pytest.mark.parametrize(
    "start,end,length",
    [
        ("2024-02-01", "2024-02-29", 29),
        ("2026-10-01", "2026-10-31", 31),
        ("2026-10-03", "2026-10-09", 7),
        ("2026-10-28", "2026-10-28", 1),
    ],
)
def test_complete_periods(start: str, end: str, length: int) -> None:
    q = AnalysisQuery(
        shop_id=uuid4(),
        platform_id="adq",
        start_date=start,
        end_date=end,
        dimensions=("ads",),
        timezone="Asia/Shanghai",
    )
    assert len(period_days(q)) == length


@pytest.mark.parametrize("start,end", [("2026-10-01", "2026-10-30"), ("2026-10-01", "2026-10-02")])
def test_incomplete_periods_rejected(start: str, end: str) -> None:
    q = AnalysisQuery(
        shop_id=uuid4(),
        platform_id="adq",
        start_date=start,
        end_date=end,
        dimensions=("ads",),
        timezone="Asia/Shanghai",
    )
    with pytest.raises(ApiError):
        period_days(q)
