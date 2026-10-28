"""持久业务提示，不承载完整对象；投影提交序与当前可见范围均由PG保证。"""

import re
from contextlib import nullcontext
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

CURSOR = re.compile(r"([1-9][0-9]{0,18}):(0|[1-9][0-9]{0,18})\Z")
MAX_BIGINT = 9223372036854775807


class EventChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: str = Field(pattern=r"^[A-Z][A-Z_]{0,29}$")


class EventData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str = Field(alias="tenantId", pattern=r"^[1-9][0-9]{0,18}$")
    entity_type: Literal["command_job", "approval", "execution"] = Field(alias="entityType")
    entity_id: UUID = Field(alias="entityId")
    version: int = Field(strict=True, gt=0, le=9007199254740991)
    changes: EventChanges
    at: AwareDatetime


class StreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    seq: int = Field(strict=True, gt=0, le=MAX_BIGINT)
    source_event_id: UUID
    event: Literal["job.changed", "approval.changed", "execution.unknown"]
    data: EventData


class EventPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    head_seq: int
    retained_from_seq: int
    cursor: str
    events: tuple[StreamEvent, ...] = ()
    has_more: bool = False
    resync_reason: Literal["CURSOR_TENANT_CHANGED", "CURSOR_OLD", "CURSOR_AHEAD"] | None = None


class CoreEventStream:
    def __init__(self, engine: Engine, identity: VerifiedIdentity) -> None:
        self.engine, self.identity = engine, identity

    def project(self, limit: int = 100, *, unit_of_work: Session | None = None) -> int:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("事件投影批量必须为1到500")
        with (
            nullcontext(unit_of_work)
            if unit_of_work is not None
            else business_transaction(self.engine, self.identity)
        ) as session:
            return int(
                session.execute(
                    text("SELECT access.project_core_events(:limit)"), {"limit": limit}
                ).scalar_one()
            )

    def prune(self) -> int:
        with business_transaction(self.engine, self.identity) as session:
            return int(session.execute(text("SELECT access.prune_core_events()")).scalar_one())

    def read(self, cursor: str | None = None, *, limit: int = 100) -> EventPage:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("事件读取批量必须为1到500")
        after, reason = 0, None
        if cursor is not None:
            match = CURSOR.fullmatch(cursor)
            if match is None or any(int(value) > MAX_BIGINT for value in match.groups()):
                raise ApiError(ErrorCode.INVALID_INPUT, "事件游标格式无效")
            tenant, after = (int(value) for value in match.groups())
            if tenant != self.identity.tenant_id:
                reason, after = "CURSOR_TENANT_CHANGED", 0
        with business_transaction(self.engine, self.identity) as session:
            # 单一MVCC快照包含水位、保留边界、当前RLS与事件，避免清理/投影间读到混合版本。
            row = session.execute(
                text("""WITH head AS (
              SELECT coalesce((SELECT head_seq FROM event_stream_heads),0) AS head_seq,
               coalesce((SELECT retained_from_seq FROM event_stream_heads),1) AS retained_from_seq
            ), visible AS (
              SELECT p.* FROM event_projections p,head
              WHERE p.tenant_seq>:after AND p.tenant_seq<=head.head_seq
              ORDER BY p.tenant_seq LIMIT :limit
            ) SELECT head.*,
              coalesce((SELECT jsonb_agg(jsonb_build_object('seq',tenant_seq,
                'source_event_id',source_event_id,'event',event_type,'data',payload)
                ORDER BY tenant_seq) FROM visible),'[]'::jsonb) AS events FROM head"""),
                {"after": after, "limit": limit + 1},
            ).one()
        head, retained = row.head_seq, row.retained_from_seq
        reason = reason or (
            "CURSOR_OLD" if after < retained - 1 else "CURSOR_AHEAD" if after > head else None
        )
        events = tuple(StreamEvent.model_validate(item) for item in row.events)
        has_more = len(events) > limit
        events = events[:limit]
        next_seq = events[-1].seq if has_more else head
        return EventPage(
            head_seq=head,
            retained_from_seq=retained,
            cursor=f"{self.identity.tenant_id}:{head if reason else next_seq}",
            events=() if reason else events,
            has_more=False if reason else has_more,
            resync_reason=reason,
        )
