"""中转站视频调用记录：本地任务标识、幂等键、冻结价格与产物元数据。

外部同步生成耗时可达数百秒，Web请求只登记本表并投递队列；实际字节与实测时长
由后台Worker写入，轮询与取回结果只读本表，不再发起任何外部请求。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, Engine, Result, text

from yuliang.platform.db import VerifiedIdentity, business_transaction


@dataclass(frozen=True)
class RelayVideoCall:
    id: UUID
    tenant_id: int
    membership_id: UUID
    actor_id: UUID
    shop_id: UUID
    request_key: UUID
    state: str
    prompt: str
    duration_seconds: int
    resolution: str
    aspect_ratio: str
    pricing_mode: str
    unit_price_micro_minor: int
    currency: str
    provider_task_id: str | None
    blob_key: str | None
    mime: str | None
    size_bytes: int | None
    sha256: str | None
    measured_duration_seconds: int | None
    settled_amount_micro_minor: int | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class VideoCallStore(Protocol):
    identity: VerifiedIdentity

    def shop_for_request(self, request_key: UUID) -> UUID | None: ...

    def create(
        self,
        *,
        request_key: UUID,
        shop_id: UUID,
        prompt: str,
        duration_seconds: int,
        resolution: str,
        aspect_ratio: str,
        pricing_mode: str,
        unit_price_micro_minor: int,
        currency: str,
    ) -> tuple[RelayVideoCall, bool]: ...

    def get(self, call_id: UUID) -> RelayVideoCall | None: ...

    def claim(self, call_id: UUID) -> RelayVideoCall | None: ...

    def succeed(
        self,
        call_id: UUID,
        *,
        provider_task_id: str,
        blob_key: str,
        mime: str,
        size_bytes: int,
        sha256: str,
        measured_duration_seconds: int,
        settled_amount_micro_minor: int,
    ) -> None: ...

    def fail(
        self, call_id: UUID, *, expected: str, error_code: str, error_message: str
    ) -> bool: ...

    def reconcile(self, older_than_seconds: int) -> int: ...


def _call(row: Any) -> RelayVideoCall:
    return RelayVideoCall(**dict(row))


def _rowcount(result: Result[Any]) -> int:
    return cast(CursorResult[Any], result).rowcount


class PostgresVideoCallStore:
    def __init__(self, engine: Engine, identity: VerifiedIdentity) -> None:
        self.engine = engine
        self.identity = identity

    def shop_for_request(self, request_key: UUID) -> UUID | None:
        with business_transaction(self.engine, self.identity) as session:
            value = session.execute(
                text(
                    "SELECT shop_id FROM generation_jobs "
                    "WHERE tenant_id=:tenant AND request_key=:key"
                ),
                dict(tenant=self.identity.tenant_id, key=request_key),
            ).scalar_one_or_none()
        return UUID(str(value)) if value is not None else None

    def create(
        self,
        *,
        request_key: UUID,
        shop_id: UUID,
        prompt: str,
        duration_seconds: int,
        resolution: str,
        aspect_ratio: str,
        pricing_mode: str,
        unit_price_micro_minor: int,
        currency: str,
    ) -> tuple[RelayVideoCall, bool]:
        with business_transaction(self.engine, self.identity) as session:
            # 同一请求键串行化，避免并发登记产生两条调用或两次投递。
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760172))"),
                dict(key=f"{self.identity.tenant_id}|{request_key}|relay-video"),
            )
            existing = (
                session.execute(
                    text(
                        "SELECT * FROM relay_video_calls "
                        "WHERE tenant_id=:tenant AND request_key=:key"
                    ),
                    dict(tenant=self.identity.tenant_id, key=request_key),
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return _call(existing), False
            row = (
                session.execute(
                    text("""INSERT INTO relay_video_calls(id,tenant_id,membership_id,actor_id,
                    shop_id,request_key,state,prompt,duration_seconds,resolution,aspect_ratio,
                    pricing_mode,unit_price_micro_minor,currency)
                    VALUES(:id,:tenant,:member,:actor,:shop,:key,'PENDING',:prompt,:duration,
                    :resolution,:aspect,:mode,:rate,:currency) RETURNING *"""),
                    dict(
                        id=uuid4(),
                        tenant=self.identity.tenant_id,
                        member=self.identity.membership_id,
                        actor=self.identity.user_id,
                        shop=shop_id,
                        key=request_key,
                        prompt=prompt,
                        duration=duration_seconds,
                        resolution=resolution,
                        aspect=aspect_ratio,
                        mode=pricing_mode,
                        rate=unit_price_micro_minor,
                        currency=currency,
                    ),
                )
                .mappings()
                .one()
            )
        return _call(row), True

    def get(self, call_id: UUID) -> RelayVideoCall | None:
        with business_transaction(self.engine, self.identity) as session:
            row = (
                session.execute(
                    text("SELECT * FROM relay_video_calls WHERE tenant_id=:tenant AND id=:id"),
                    dict(tenant=self.identity.tenant_id, id=call_id),
                )
                .mappings()
                .first()
            )
        return _call(row) if row is not None else None

    def claim(self, call_id: UUID) -> RelayVideoCall | None:
        with business_transaction(self.engine, self.identity) as session:
            row = (
                session.execute(
                    text("""UPDATE relay_video_calls SET state='RUNNING',
                    updated_at=clock_timestamp()
                    WHERE tenant_id=:tenant AND id=:id AND membership_id=:member
                    AND actor_id=:actor AND state='PENDING' RETURNING *"""),
                    dict(
                        tenant=self.identity.tenant_id,
                        id=call_id,
                        member=self.identity.membership_id,
                        actor=self.identity.user_id,
                    ),
                )
                .mappings()
                .first()
            )
        return _call(row) if row is not None else None

    def succeed(
        self,
        call_id: UUID,
        *,
        provider_task_id: str,
        blob_key: str,
        mime: str,
        size_bytes: int,
        sha256: str,
        measured_duration_seconds: int,
        settled_amount_micro_minor: int,
    ) -> None:
        with business_transaction(self.engine, self.identity) as session:
            updated = _rowcount(
                session.execute(
                    text("""UPDATE relay_video_calls SET state='SUCCEEDED',
                provider_task_id=:provider,blob_key=:blob,mime=:mime,size_bytes=:size,
                sha256=:sha,measured_duration_seconds=:duration,
                settled_amount_micro_minor=:settled,updated_at=clock_timestamp()
                WHERE tenant_id=:tenant AND id=:id AND membership_id=:member
                AND actor_id=:actor AND state='RUNNING'"""),
                    dict(
                        tenant=self.identity.tenant_id,
                        id=call_id,
                        member=self.identity.membership_id,
                        actor=self.identity.user_id,
                        provider=provider_task_id,
                        blob=blob_key,
                        mime=mime,
                        size=size_bytes,
                        sha=sha256,
                        duration=measured_duration_seconds,
                        settled=settled_amount_micro_minor,
                    ),
                )
            )
        if not updated:
            raise RuntimeError("视频调用记录已不是运行中，未写入成功结果")

    def fail(self, call_id: UUID, *, expected: str, error_code: str, error_message: str) -> bool:
        if expected not in {"PENDING", "RUNNING"}:
            raise ValueError("视频调用失败只能从PENDING或RUNNING登记")
        with business_transaction(self.engine, self.identity) as session:
            updated = _rowcount(
                session.execute(
                    text("""UPDATE relay_video_calls SET state='FAILED',error_code=:code,
                error_message=:message,updated_at=clock_timestamp()
                WHERE tenant_id=:tenant AND id=:id AND membership_id=:member
                AND actor_id=:actor AND state=:expected"""),
                    dict(
                        tenant=self.identity.tenant_id,
                        id=call_id,
                        member=self.identity.membership_id,
                        actor=self.identity.user_id,
                        expected=expected,
                        code=error_code,
                        message=error_message,
                    ),
                )
            )
        return bool(updated)

    def reconcile(self, older_than_seconds: int) -> int:
        """只标记超时的RUNNING为结果未知；绝不重跑已发出的外部调用。"""
        if type(older_than_seconds) is not int or not 60 <= older_than_seconds <= 86400:
            raise ValueError("视频对账阈值须为60至86400秒")
        with business_transaction(self.engine, self.identity) as session:
            updated = _rowcount(
                session.execute(
                    text("""UPDATE relay_video_calls SET state='FAILED',
                error_code='OUTCOME_UNKNOWN',
                error_message='进程重启后调用结果未知，需人工核对供应商账单',
                updated_at=clock_timestamp()
                WHERE tenant_id=:tenant AND state='RUNNING'
                AND updated_at<=clock_timestamp()-(:seconds*interval '1 second')"""),
                    dict(tenant=self.identity.tenant_id, seconds=older_than_seconds),
                )
            )
        return int(updated)
