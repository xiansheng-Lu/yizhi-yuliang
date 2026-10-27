"""来源版本优先；抓取时间从不作为覆盖旧事实的依据。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeVar
from uuid import UUID

Decision = Literal["APPLIED", "DUPLICATE", "STALE", "CONFLICT"]
Ordered = TypeVar("Ordered", int, datetime)


@dataclass(frozen=True)
class SourceStamp:
    source_id: UUID
    sequence: int
    updated_at: datetime | None
    version: int | None
    version_stream: str | None


def compare(previous: SourceStamp, incoming: SourceStamp, *, same_value: bool) -> Decision:
    def ordered(old: Ordered, new: Ordered) -> Decision:
        if new > old:
            return "APPLIED"
        if new < old:
            return "STALE"
        return "DUPLICATE" if same_value else "CONFLICT"

    if previous.version is not None:
        if incoming.version is None or previous.version_stream != incoming.version_stream:
            return "CONFLICT"
        return ordered(previous.version, incoming.version)
    if incoming.version is not None:
        if previous.source_id == incoming.source_id and incoming.sequence > previous.sequence:
            return "APPLIED"
        if previous.updated_at is not None and incoming.updated_at is not None:
            return ordered(previous.updated_at, incoming.updated_at)
        return "CONFLICT"
    if previous.updated_at is not None:
        if incoming.updated_at is None:
            return "CONFLICT"
        return ordered(previous.updated_at, incoming.updated_at)
    if incoming.updated_at is not None:
        return (
            "APPLIED"
            if previous.source_id == incoming.source_id and incoming.sequence > previous.sequence
            else "CONFLICT"
        )
    if previous.source_id != incoming.source_id:
        return "CONFLICT"
    return ordered(previous.sequence, incoming.sequence)
