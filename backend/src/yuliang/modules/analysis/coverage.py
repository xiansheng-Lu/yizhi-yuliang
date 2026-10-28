"""冻结日期query推导日/周/月，拒绝缩期和非连续周期。"""

import calendar
from datetime import date, timedelta

from yuliang.modules.analysis.models import AnalysisQuery
from yuliang.platform.errors import ApiError, ErrorCode


def period_days(query: AnalysisQuery) -> tuple[date, ...]:
    start = date.fromisoformat(query.start_date)
    end = date.fromisoformat(query.end_date)
    length = (end - start).days + 1
    if not (
        length in {1, 7}
        or (
            start.day == 1
            and start.year == end.year
            and start.month == end.month
            and end.day == calendar.monthrange(end.year, end.month)[1]
        )
    ):
        raise ApiError(ErrorCode.INVALID_INPUT, "分析周期须为日、连续七天或完整自然月")
    return tuple(start + timedelta(days=i) for i in range(length))
