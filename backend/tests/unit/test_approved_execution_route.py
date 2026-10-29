"""Approved execution must use the side-effect queue before the system wildcard."""

import pytest

from yuliang.settings import Settings
from yuliang.worker.app import create_celery

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("task", "queue"),
    [
        ("yuliang.system.approved_execution", "execute"),
        ("yuliang.execute.guard", "execute"),
        ("yuliang.system.identity_probe", "system"),
        ("yuliang.ai_media.relay_video_generate", "ai-media"),
    ],
)
def test_actual_celery_routing_preserves_execution_queue(
    settings: Settings, task: str, queue: str
) -> None:
    instance = create_celery(settings)
    try:
        routed = instance.amqp.router.route({}, task)
        assert routed["queue"].name == f"{settings.worker_queue_prefix}.{queue}"
    finally:
        instance.close()
