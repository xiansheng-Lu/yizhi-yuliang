"""有界真实依赖探测，短缓存避免探针并发放大；不读取租户业务明细。"""

import threading
import time
from dataclasses import dataclass
from typing import Any

import redis
from kombu import Connection  # type: ignore[import-untyped]
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from yuliang.settings import Settings


@dataclass(frozen=True)
class ProbeSnapshot:
    postgres_app: bool
    postgres_auth: bool
    redis: bool
    rabbitmq: bool
    checked_at: float
    identity_enabled: bool

    @property
    def ready(self) -> bool:
        return self.postgres_app and (self.postgres_auth or not self.identity_enabled)

    @property
    def dispatch_ready(self) -> bool:
        return self.postgres_app and self.rabbitmq

    def data(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "degraded": not all((self.postgres_app, self.postgres_auth, self.redis, self.rabbitmq)),
            "dependencies": {
                key: "UP" if getattr(self, key) else "DOWN"
                for key in ("postgres_app", "postgres_auth", "redis", "rabbitmq")
            },
            "databaseAvailable": self.postgres_app,
            "dispatchTransportReady": self.dispatch_ready,
            "sessionAuthority": "POSTGRESQL",
            "cacheFallback": "POSTGRESQL" if not self.redis else "AVAILABLE",
            "workerState": "UNOBSERVED",
            "businessExecutionEnabled": False,
            "checkedAtEpoch": self.checked_at,
        }

    def metrics(self) -> str:
        lines = [
            "# HELP yuliang_dependency_up Latest bounded dependency connection probe",
            "# TYPE yuliang_dependency_up gauge",
        ]
        for key in ("postgres_app", "postgres_auth", "redis", "rabbitmq"):
            lines.append(f'yuliang_dependency_up{{dependency="{key}"}} {int(getattr(self, key))}')
        for name, value in (
            ("api_ready", int(self.ready)),
            ("dispatch_transport_ready", int(self.dispatch_ready)),
            ("dependency_checked_at_seconds", self.checked_at),
        ):
            lines.extend([f"# TYPE yuliang_{name} gauge", f"yuliang_{name} {value}"])
        return "\n".join(lines) + "\n"


class ProbeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.databases = {
            name: create_engine(
                url.get_secret_value(),
                hide_parameters=True,
                poolclass=NullPool,
                connect_args={"connect_timeout": 1, "options": "-c statement_timeout=1000"},
            )
            for name, url in (
                ("postgres_app", settings.database_url),
                ("postgres_auth", settings.auth_database_url),
            )
        }
        self.cache = redis.Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            retry=Retry(NoBackoff(), 0),
        )
        self.lock = threading.Lock()
        self.previous: ProbeSnapshot | None = None
        self.next_probe = 0.0

    def close(self) -> None:
        for engine in self.databases.values():
            engine.dispose()
        self.cache.close()

    def snapshot(self) -> ProbeSnapshot:
        with self.lock:
            if self.previous is not None and time.monotonic() < self.next_probe:
                return self.previous
            states: dict[str, bool] = {}
            for name, engine in self.databases.items():
                try:
                    with engine.connect() as connection:
                        states[name] = connection.execute(text("SELECT 1")).scalar_one() == 1
                except Exception:
                    states[name] = False
            try:
                states["redis"] = bool(self.cache.ping())
            except Exception:
                states["redis"] = False
            try:
                with Connection(
                    self.settings.broker_url.get_secret_value(),
                    connect_timeout=1,
                    transport_options={"read_timeout": 1, "write_timeout": 1},
                ) as broker:
                    broker.ensure_connection(max_retries=0)
                    states["rabbitmq"] = broker.connected
            except Exception:
                states["rabbitmq"] = False
            self.previous = ProbeSnapshot(
                **states,
                checked_at=time.time(),
                identity_enabled=self.settings.identity_api_enabled,
            )
            self.next_probe = time.monotonic() + 1
            return self.previous
