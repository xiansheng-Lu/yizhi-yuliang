"""四队列；已登记副作用任务重投仅核对，平台写不自动重发。"""

from celery import Celery  # type: ignore[import-untyped]
from kombu import Exchange, Queue  # type: ignore[import-untyped]

from yuliang.settings import Settings

KINDS = ("execute", "platform", "ai-media", "system")


def create_celery(settings: Settings) -> Celery:
    instance = Celery(
        "yuliang",
        broker=settings.broker_url.get_secret_value(),
        backend=settings.redis_url.get_secret_value(),
        include=["yuliang.worker.tasks"],
    )
    prefix = settings.worker_queue_prefix
    exchange = Exchange(prefix, type="direct", durable=True)
    queues = {kind: f"{prefix}.{kind}" for kind in KINDS}
    instance.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_accept_content=["json"],
        task_create_missing_queues=False,
        task_queues=[
            Queue(name, exchange=exchange, routing_key=name, durable=True)
            for name in queues.values()
        ],
        task_default_queue=queues["system"],
        task_default_exchange=prefix,
        task_default_exchange_type="direct",
        task_default_routing_key=queues["system"],
        task_routes={
            "yuliang.system.approved_execution": {
                "queue": queues["execute"],
                "routing_key": queues["execute"],
            },
            **{
                f"yuliang.{kind.replace('-', '_')}.*": {"queue": name, "routing_key": name}
                for kind, name in queues.items()
            },
        },
        task_acks_late=True,
        task_default_delivery_mode="persistent",
        task_acks_on_failure_or_timeout=True,
        task_reject_on_worker_lost=False,
        task_publish_retry=False,
        broker_connection_retry_on_startup=False,
        broker_transport_options={"confirm_publish": True},
        worker_prefetch_multiplier=1,
        worker_send_task_events=False,
        result_expires=3600,
        beat_scheduler="yuliang.worker.scheduler:SingletonScheduler",
        beat_max_loop_interval=3,
        timezone="Asia/Shanghai",
        enable_utc=True,
        yuliang_settings=settings,
    )
    return instance


app = create_celery(Settings())
