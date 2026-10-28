"""New event types remain minimal read hints, not frozen object snapshots."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.api.events import event_frame
from yuliang.platform.events import StreamEvent


@pytest.mark.parametrize(
    "entity,topic", [("analysis_task", "analysis.changed"), ("workflow_run", "workflow.changed")]
)
def test_dedicated_real_entity_topics_serialize_without_job_impersonation(
    entity: str, topic: str
) -> None:
    event = StreamEvent.model_validate(
        dict(
            seq=1,
            source_event_id=uuid4(),
            event=topic,
            data=dict(
                tenantId="1",
                entityType=entity,
                entityId=uuid4(),
                version=1,
                changes=dict(status="RUNNING"),
                at=datetime.now(UTC),
            ),
        )
    )
    frame = event_frame(1, event)
    assert "event: " + topic in frame and entity in frame and "command_job" not in frame


@pytest.mark.parametrize("field", ["snapshot", "token", "payload", "progress"])
def test_status_hint_cannot_carry_secret_or_unfrozen_progress(field: str) -> None:
    with pytest.raises(ValidationError):
        StreamEvent.model_validate(
            dict(
                seq=1,
                source_event_id=uuid4(),
                event="analysis.changed",
                data=dict(
                    tenantId="1",
                    entityType="analysis_task",
                    entityId=uuid4(),
                    version=1,
                    changes=dict(status="RUNNING") | {field: "private"},
                    at=datetime.now(UTC),
                ),
            )
        )
