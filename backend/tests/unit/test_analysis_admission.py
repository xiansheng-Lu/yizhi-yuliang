"""真实自然日与分析条件、阶段语义；不做轮询假进度。"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.analysis.models import AnalysisCreate, AnalysisQuery, AnalysisTask

pytestmark = pytest.mark.unit


def query() -> dict[str, Any]:
    return dict(
        shopId=str(uuid4()),
        platformId="all",
        startDate="2024-01-01",
        endDate="2024-12-31",
        dimensions=["shop", "ads"],
        timezone="Asia/Shanghai",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"startDate": "2026-02-29"},
        {"endDate": "2025-01-01"},
        {"endDate": "2023-12-31"},
        {"startDate": "2024-01-01T00:00:00Z"},
        {"dimensions": []},
        {"dimensions": ["ads", "ads"]},
        {"dimensions": ["profit"]},
        {"timezone": "UTC"},
        {"platformId": "ocean"},
        {"userId": str(uuid4())},
    ],
)
def test_invalid_or_untrusted_analysis_dimensions(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AnalysisQuery.model_validate(query() | changes)


def test_leap_year_366_day_range_and_extra_submission_state() -> None:
    value = AnalysisCreate.model_validate(dict(query=query(), idempotencyKey=str(uuid4())))
    assert value.query.end_date == "2024-12-31"
    with pytest.raises(ValidationError):
        AnalysisCreate.model_validate(
            dict(query=query(), idempotencyKey=str(uuid4()), status="succeeded")
        )


@pytest.mark.parametrize(
    "state",
    [
        dict(status="QUEUED", progress=0),
        dict(status="queued", progress=1),
        dict(status="running", progress=100),
        dict(status="failed", progress=1),
        dict(status="succeeded", progress=100),
        dict(status="succeeded", progress=90, resultId=str(uuid4())),
    ],
)
def test_progress_and_result_must_represent_real_stage(state: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AnalysisTask.model_validate(
            dict(id=uuid4(), query=query(), createdAt=datetime.now(UTC)) | state
        )
