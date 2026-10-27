"""真实Redis原子准入、熔断和探测栅栏；不授予平台业务能力。"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import redis
from pydantic import SecretStr
from redis.backoff import NoBackoff
from redis.retry import Retry

from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.outcomes import Outcome

Lane = Literal["SYNC", "EXECUTE", "READBACK", "PROBE"]


class GovernanceUnavailable(Exception):
    def __init__(self) -> None:
        super().__init__("平台准入设施暂不可用，禁止新调用")


@dataclass(frozen=True)
class QuotaLimits:
    app_limit: int
    account_limit: int
    app_execute_reserve: int
    app_readback_reserve: int
    account_execute_reserve: int
    account_readback_reserve: int
    window_ms: int
    failure_threshold: int = 3
    open_ms: int = 30000
    probe_lease_ms: int = 10000
    alert_quiet_ms: int = 30000

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            if type(value) is not int or value < 0:
                raise ValueError("配额参数必须为非负整数")
        if not (
            1 <= self.app_limit <= 1000000
            and 1 <= self.account_limit <= 1000000
            and 100 <= self.window_ms <= 86400000
            and 1 <= self.failure_threshold <= 100
            and 100 <= self.open_ms <= 86400000
            and 100 <= self.probe_lease_ms <= 60000
            and 100 <= self.alert_quiet_ms <= 86400000
        ):
            raise ValueError("配额或熔断参数超出边界")
        if (
            self.app_execute_reserve + self.app_readback_reserve > self.app_limit
            or self.account_execute_reserve + self.account_readback_reserve > self.account_limit
        ):
            raise ValueError("保留配额不能超过总配额")


@dataclass(frozen=True)
class QuotaScope:
    application_id: UUID
    ctx: GatewayContext

    def digests(self) -> tuple[str, str]:
        app = hashlib.sha256(f"{self.ctx.provider}|{self.application_id}".encode()).hexdigest()
        account = hashlib.sha256(
            f"{self.ctx.identity.tenant_id}|{self.ctx.account_id}|{self.ctx.connector_id}|{self.ctx.product_line}".encode()
        ).hexdigest()
        return app, account


@dataclass(frozen=True)
class Admission:
    scope: QuotaScope
    allowed: bool
    reason: str
    server_ms: int
    retry_at_ms: int
    app_epoch: int
    account_epoch: int
    probe_token: str

    @property
    def retry_at(self) -> datetime | None:
        return (
            datetime.fromtimestamp(self.retry_at_ms / 1000, UTC)
            if self.retry_at_ms > self.server_ms
            else None
        )


class CallDeferred(Exception):
    def __init__(self, decision: Admission) -> None:
        super().__init__("平台调用延期，不能立即重发")
        self.decision = decision


class PlatformGovernance:
    def __init__(
        self,
        client: redis.Redis,
        limits: QuotaLimits,
        *,
        namespace: str = "yuliang:platform:v1",
        alert_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,120}", namespace):
            raise ValueError("准入命名空间不合法")
        self.client, self.limits, self.namespace, self.alert_sink = (
            client,
            limits,
            namespace,
            alert_sink,
        )
        self._config = json.dumps(asdict(limits), sort_keys=True)
        self._admit = Path(__file__).with_name("governance_admit.lua").read_text(encoding="utf-8")
        self._report = Path(__file__).with_name("governance_report.lua").read_text(encoding="utf-8")

    @classmethod
    def from_url(cls, url: SecretStr, limits: QuotaLimits, **kwargs: Any) -> "PlatformGovernance":
        client = redis.Redis.from_url(
            url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
        )
        return cls(client, limits, **kwargs)

    def keys(self, scope: QuotaScope) -> tuple[str, ...]:
        app, account = scope.digests()
        prefix = f"{self.namespace}:{{{app}}}"
        return (
            prefix + ":bucket",
            prefix + ":account:" + account + ":bucket",
            prefix + ":circuit",
            prefix + ":account:" + account + ":circuit",
            prefix + ":alert",
            prefix + ":account:" + account + ":alert",
        )

    def admit(self, scope: QuotaScope, lane: Lane) -> Admission:
        if lane not in {"SYNC", "EXECUTE", "READBACK", "PROBE"}:
            raise ValueError("准入通道不合法")
        keys = self.keys(scope)
        try:
            raw = self.client.eval(self._admit, 4, *keys[:4], self._config, lane, str(uuid4()))
            data = json.loads(cast(str, raw))  # 同步Redis客户端的上游类型也包含异步联合。
            if not isinstance(data, dict) or type(data.get("allowed")) is not bool:
                raise ValueError("准入结果不合法")
            return Admission(
                scope,
                data["allowed"],
                str(data["reason"]),
                int(data["server_ms"]),
                int(data["retry_at_ms"]),
                int(data["app_epoch"]),
                int(data["account_epoch"]),
                str(data["probe_token"]),
            )
        except (redis.RedisError, ValueError, TypeError, KeyError, OverflowError):
            raise GovernanceUnavailable() from None

    def report(
        self,
        ticket: Admission,
        outcome: Outcome,
        *,
        rate_scope: Literal["APPLICATION", "ACCOUNT", "UNKNOWN"] = "UNKNOWN",
    ) -> dict[str, Any]:
        if not ticket.allowed or rate_scope not in {"APPLICATION", "ACCOUNT", "UNKNOWN"}:
            raise ValueError("只有已准入请求才能报告结果")
        if outcome.category == "RATE_LIMITED":
            kind = "RATE_LIMITED"
        elif outcome.external in {"ACCEPTED", "READ_SUCCEEDED"}:
            kind = "SUCCESS"
        elif outcome.category in {
            "TIMEOUT",
            "UPSTREAM_ERROR",
            "RESPONSE_TOO_LARGE",
            "INVALID_RESPONSE",
        }:
            kind = "FAILURE"
        else:
            kind = "NEUTRAL"
        retry_ms = (
            outcome.retry_after_seconds if outcome.retry_after_seconds is not None else 1
        ) * 1000
        if not 0 <= retry_ms <= 86400000:
            raise ValueError("退避时间超出边界")
        retry_ms = max(1000, retry_ms)
        try:
            data = json.loads(
                cast(
                    str,
                    self.client.eval(
                        self._report,
                        7,
                        *self.keys(ticket.scope),
                        self.keys(ticket.scope)[0] + ":report:" + ticket.probe_token,
                        self._config,
                        kind,
                        ticket.app_epoch,
                        ticket.account_epoch,
                        ticket.probe_token,
                        retry_ms,
                        "1" if outcome.retry_after_unconfirmed else "0",
                        rate_scope,
                    ),
                )
            )
            if (
                not isinstance(data, dict)
                or type(data.get("alert")) is not bool
                or type(data.get("changed")) is not int
            ):
                raise ValueError("反馈结果不合法")
        except (redis.RedisError, ValueError, TypeError):
            raise GovernanceUnavailable() from None
        delivered = None
        if data.get("alert") and self.alert_sink is not None:
            try:
                self.alert_sink(
                    {
                        "tenantId": ticket.scope.ctx.identity.tenant_id,
                        "accountId": str(ticket.scope.ctx.account_id),
                        "provider": ticket.scope.ctx.provider,
                        "category": outcome.category,
                        "requestId": str(ticket.scope.ctx.request_id),
                    }
                )
                delivered = True
            except Exception:
                delivered = False
        return {
            "changed": int(data["changed"]),
            "alert": bool(data["alert"]),
            "alertDelivered": delivered,
        }

    def inspect(self, scope: QuotaScope) -> dict[str, dict[str, str]]:
        try:
            values = [
                cast(dict[Any, Any], self.client.hgetall(key)) for key in self.keys(scope)[2:4]
            ]
        except redis.RedisError:
            raise GovernanceUnavailable() from None

        def strings(value: dict[Any, Any]) -> dict[str, str]:
            return {
                (key.decode() if isinstance(key, bytes) else str(key)): (
                    item.decode() if isinstance(item, bytes) else str(item)
                )
                for key, item in value.items()
            }

        return {"application": strings(values[0]), "account": strings(values[1])}

    def release_manual(self, scope: QuotaScope, proof_ref: str) -> None:
        """仅供服务端维护入口在独立核对后调用；恢复为待探测，不直接开放业务。"""
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", proof_ref):
            raise ValueError("需要独立核对证据引用")
        script = """
          for i=1,2 do
           redis.call('HDEL',KEYS[i],'manual')
           redis.call('HINCRBY',KEYS[i],'epoch',1)
           redis.call('HSET',KEYS[i],'state','OPEN','open_until',0,'cooldown_until',0,
             'probe_until',0,'probe_token','','release_proof',ARGV[1])
          end
          return 1
        """
        try:
            self.client.eval(
                script, 2, *self.keys(scope)[2:4], hashlib.sha256(proof_ref.encode()).hexdigest()
            )
        except redis.RedisError:
            raise GovernanceUnavailable() from None
