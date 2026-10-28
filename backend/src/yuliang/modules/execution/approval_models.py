"""审批输入和后台白名单；资格结论不能代替数据库审批证据。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from yuliang.modules.command.jobs import BooleanValue, CommandJob, MoneyValue


class ReviewJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    job_id: UUID
    expected_version: int = Field(strict=True, gt=0)
    decision: Literal["APPROVE", "REJECT"]
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("comment")
    @classmethod
    def normalized_comment(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ServerApprovalPermit(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    id: UUID
    tenant_id: int = Field(strict=True, gt=0)
    policy_id: UUID
    policy_version: int = Field(strict=True, gt=0)
    policy_actor_membership_id: UUID
    account_id: UUID
    campaign_id: UUID
    action: Literal["CHANGE_DAILY_BUDGET", "CHANGE_BID", "PAUSE_AD_PLAN"]
    max_change_minor: int | None = Field(default=None, strict=True, gt=0, le=9007199254740991)
    max_change_percent: Decimal | None = Field(default=None, gt=0, le=100)
    important: bool | None = Field(default=None, strict=True)
    classification_ref: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:/-]{1,180}$")
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    proof_ref: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,180}$")
    active: bool = Field(strict=True)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.valid_until <= self.valid_from:
            raise ValueError("需要明确有效的策略批准时间窗")
        if self.action == "PAUSE_AD_PLAN":
            if self.important is not False or not self.classification_ref:
                raise ValueError("暂停的后台白名单需要明确非重要分类依据")
        elif self.max_change_minor is None or self.max_change_percent is None:
            raise ValueError("减额白名单必须同时限制金额与幅度")
        return self


def human_required(job: CommandJob, *, important: bool | None = None) -> tuple[str, ...]:
    """未证实安全的类型保守人审，不从动作名称推断可扩大消耗。"""
    old, new = job.old_value, job.new_value
    if job.target_type != "AD_PLAN":
        return ("MANUAL_HANDOFF",)
    if (
        job.action_type == "CHANGE_DAILY_BUDGET"
        and isinstance(old, MoneyValue)
        and isinstance(new, MoneyValue)
    ):
        return ("BUDGET_INCREASE",) if new.amount_minor > old.amount_minor else ()
    if (
        job.action_type == "CHANGE_BID"
        and isinstance(old, MoneyValue)
        and isinstance(new, MoneyValue)
    ):
        return ("EXPOSURE_INCREASE",) if new.amount_minor > old.amount_minor else ()
    if job.action_type == "PAUSE_AD_PLAN":
        return ("IMPORTANT_OR_UNCLASSIFIED_PAUSE",) if important is not False else ()
    return ("EXPOSURE_INCREASE_OR_UNPROVEN_SAFE",)


def permit_matches(
    job: CommandJob,
    permit: ServerApprovalPermit,
    *,
    at: datetime,
    actor_membership: UUID,
) -> bool:
    """只供后台服务解释资格；落账函数仍须独立核对数据库当前记录与风险。"""
    if (
        not permit.active
        or not permit.valid_from <= at < permit.valid_until
        or str(permit.tenant_id) != job.tenant_id
        or str(permit.account_id) != job.account_id
        or str(permit.campaign_id) != job.target_id
        or permit.policy_actor_membership_id != actor_membership
        or permit.policy_version != job.risk_summary.policy_version
        or permit.action != job.action_type
        or human_required(job, important=permit.important)
    ):
        return False
    old, new = job.old_value, job.new_value
    if permit.action == "PAUSE_AD_PLAN":
        return (
            isinstance(old, BooleanValue)
            and old.value is True
            and isinstance(new, BooleanValue)
            and not new.value
        )
    if not isinstance(old, MoneyValue) or not isinstance(new, MoneyValue):
        return False
    delta = old.amount_minor - new.amount_minor
    return (
        old.amount_minor > 0
        and delta > 0
        and permit.max_change_minor is not None
        and permit.max_change_percent is not None
        and delta <= permit.max_change_minor
        and Decimal(delta) * 100 <= Decimal(old.amount_minor) * permit.max_change_percent
    )
