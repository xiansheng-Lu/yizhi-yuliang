"""Redis租约限制一个Beat调度器；Redis不可用时停止调度。"""

import secrets
from typing import Any

import redis
from celery.beat import Scheduler  # type: ignore[import-untyped]

RENEW = (
    "if redis.call('get',KEYS[1])==ARGV[1] then "
    "return redis.call('expire',KEYS[1],ARGV[2]) else return 0 end"
)
RELEASE = (
    "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"
)


class BeatLease:
    def __init__(self, client: redis.Redis, prefix: str, seconds: int = 15) -> None:
        self.client, self.key = client, f"{prefix}:beat-lease"
        self.token, self.seconds = secrets.token_hex(32), seconds

    def acquire(self) -> bool:
        if self.client.eval(RENEW, 1, self.key, self.token, self.seconds):
            return True
        return bool(self.client.set(self.key, self.token, nx=True, ex=self.seconds))

    def release(self) -> None:
        self.client.eval(RELEASE, 1, self.key, self.token)


# Celery未发布类型标记，仅允许此类边界继承。
class SingletonScheduler(Scheduler):  # type: ignore[misc]
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        settings = self.app.conf.yuliang_settings
        self.client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(), socket_connect_timeout=1, socket_timeout=1
        )
        self.lease = BeatLease(self.client, settings.worker_queue_prefix)

    def tick(self, *args: Any, **kwargs: Any) -> float:
        try:
            if not self.lease.acquire():
                return 3.0
            return float(super().tick(*args, **kwargs))
        except redis.RedisError:
            return 3.0

    def close(self) -> None:
        try:
            self.lease.release()
        except redis.RedisError:
            pass
        finally:
            self.client.close()
            super().close()

    def apply_entry(self, entry: Any, producer: Any = None) -> None:
        # 每个发布前重新检查租约；交付幂等仍由后续Outbox/Inbox保证。
        try:
            if self.lease.acquire():
                super().apply_entry(entry, producer=producer)
        except redis.RedisError:
            return
