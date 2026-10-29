"""内部时间线；历史事件与当前可变快照严格分开，无前端新合同。"""

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field, model_validator

from yuliang.modules.command.jobs import JobStatus

SourceKind = Literal[
    "JOB_EVENT",
    "APPROVAL",
    "APPROVAL_DECISION",
    "ATTEMPT",
    "RECEIPT",
    "RECOVERY_EVIDENCE",
    "READ_TRANSPORT",
    "VERIFIED_FACT",
    "APPROVED_ORIGIN",
    "DISPATCH",
    "HUMAN_NOTE",
]
EvidenceLevel = Literal["E0", "E1", "E2", "UNVERIFIED_NOTE"]
SUMMARIES: dict[str, str] = {
    "JOB_EVENT": "已记录申请状态转移",
    "APPROVAL": "审批记录已创建，创建时版本未知",
    "APPROVAL_DECISION": "已记录不可变审批决定",
    "ATTEMPT": "本地已登记SENT尝试，不代表平台受理",
    "RECEIPT": "已登记原始记录驱动的执行回执，不代表配置生效",
    "RECOVERY_EVIDENCE": "已记录恢复观察证据",
    "READ_TRANSPORT": "已记录只读传输观察，不代表配置匹配",
    "VERIFIED_FACT": "已持久记录配置匹配核验事实",
    "APPROVED_ORIGIN": "已生成真实批准来源事件，不代表已派发",
    "DISPATCH": "派发记录已创建，当前状态仅见当前快照",
    "HUMAN_NOTE": "人工陈述仅供跟进，未经执行证据核验",
}


class TimelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class TimelineEvent(TimelineModel):
    source_kind: SourceKind
    source_id: str = Field(
        pattern=r"^(?:[1-9][0-9]{0,19}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$"
    )
    at: AwareDatetime
    evidence_level: EvidenceLevel
    job_version: int | None = Field(default=None, strict=True, gt=0)
    source_version: int | None = Field(default=None, strict=True, gt=0)
    attempt_id: UUID | None = None
    before_status: JobStatus | None = None
    after_status: JobStatus | None = None
    decision: Literal["APPROVE", "REJECT"] | None = None
    accepted: bool | None = Field(default=None, strict=True)
    applied: bool | None = Field(default=None, strict=True)
    disposition: (
        Literal[
            "APPLIED", "STALE_FENCE", "STALE_ATTEMPT", "STALE_VERSION", "STALE_EVIDENCE", "TERMINAL"
        ]
        | None
    ) = None
    recovery_kind: (
        Literal["NOT_SENT", "UNKNOWN", "TERMINAL", "STALE_GUARD", "AUTH_BLOCKED", "ERROR_BLOCKED"]
        | None
    ) = None
    note_kind: Literal["FOLLOW_UP", "SUPPORT_REFERENCE", "MANUAL_REVIEW"] | None = None
    warnings: tuple[Literal["HISTORICAL_VERSION_UNKNOWN"], ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        return f"{self.source_kind}:{self.source_id}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> str:
        return SUMMARIES[self.source_kind]

    @model_validator(mode="after")
    def evidence_boundary(self) -> Self:
        if (self.evidence_level == "E2") != (self.source_kind == "VERIFIED_FACT"):
            raise ValueError("E2只能来自持久verified fact")
        if self.source_kind == "HUMAN_NOTE" and (
            self.evidence_level != "UNVERIFIED_NOTE" or self.note_kind is None
        ):
            raise ValueError("人工陈述不得继承执行证明")
        if self.evidence_level == "UNVERIFIED_NOTE" and self.source_kind != "HUMAN_NOTE":
            raise ValueError("人工陈述来源不匹配")
        if self.evidence_level == "E1" and self.source_kind not in {"RECEIPT", "RECOVERY_EVIDENCE"}:
            raise ValueError("本地登记或只读传输不能提升为E1")
        if self.source_kind in {"APPROVAL", "DISPATCH"} and (
            self.job_version is not None
            or self.source_version is not None
            or "HISTORICAL_VERSION_UNKNOWN" not in self.warnings
        ):
            raise ValueError("可变行不能推断历史版本")
        return self


SnapshotState = Literal[
    "DRAFT",
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "EXECUTING",
    "VERIFYING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "EXPIRED",
    "BLOCKED",
    "UNKNOWN",
    "PARTIAL",
    "MANUAL",
    "EFFECTIVE",
    "PENDING",
    "SENT",
    "ACK_ACCEPTED",
    "DUE",
    "WAITING",
    "DONE",
    "BLOCKED_AUTH",
    "MANUAL_REQUIRED",
    "BLOCKED_EXECUTOR",
    "ADMITTED",
    "RECOVERY_REQUIRED",
    "RECEIPT_RECORDED",
]


class CurrentSnapshot(TimelineModel):
    source_kind: Literal["JOB", "ORDER", "ATTEMPT", "RECOVERY", "APPROVAL", "DISPATCH"]
    source_id: UUID
    state: SnapshotState
    version: int | None = Field(default=None, strict=True, gt=0)
    updated_at: AwareDatetime | None
    interpretation: Literal["CURRENT_SNAPSHOT_NOT_HISTORICAL_EVENT"] = (
        "CURRENT_SNAPSHOT_NOT_HISTORICAL_EVENT"
    )


class ExecutionTimeline(TimelineModel):
    job_id: UUID
    as_of: AwareDatetime
    events: tuple[TimelineEvent, ...] = Field(max_length=1000)
    current_snapshots: tuple[CurrentSnapshot, ...] = Field(max_length=1000)
    warnings: tuple[
        Literal[
            "INTERNAL_TIMELINE_NO_HTTP_OR_FRONTEND_CONTRACT",
            "CURRENT_SNAPSHOTS_ARE_NOT_EVENT_HISTORY",
            "HUMAN_NOTES_ARE_UNVERIFIED",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def complete_ordered(self) -> Self:
        keys = [(event.at, event.source_kind, event.source_id) for event in self.events]
        if keys != sorted(keys) or len({event.id for event in self.events}) != len(self.events):
            raise ValueError("时间线来源重复或顺序不稳定")
        return self
