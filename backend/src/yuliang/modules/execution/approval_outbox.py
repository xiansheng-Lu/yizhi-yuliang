"""真实批准来源→可信原申请者；broker信封只是地址，不是执行授权。"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode
from yuliang.platform.messages import WorkMessage

EVENT_TYPE = "yuliang.system.approved_execution"


class ApprovedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    tenant_id: int = Field(strict=True, gt=0)
    event_id: UUID
    job_id: UUID
    approved_job_version: int = Field(strict=True, gt=0)
    decision_id: UUID
    approval_id: UUID
    subject_user_id: UUID
    subject_membership_id: UUID

    @property
    def identity(self) -> VerifiedIdentity:
        return VerifiedIdentity(self.tenant_id, self.subject_membership_id, self.subject_user_id)

    def message(self) -> WorkMessage:
        return WorkMessage(
            event_id=self.event_id,
            tenant_id=self.tenant_id,
            job_id=self.job_id,
            membership_id=self.subject_membership_id,
            user_id=self.subject_user_id,
        )


class ApprovedOrigin(ApprovedEvent):
    """仅供核对/持久阻断；此身份信息不授予当前CLAIM/发送资格。"""

    subject_active: bool = Field(strict=True)


def _load[T: ApprovedEvent](
    session: Session, item: WorkMessage, model: type[T], *, origin: bool
) -> T:
    """调用者已绑定候选tenant/actor；来源复核前不得用broker身份调用执行器。"""
    try:
        value = session.execute(
            text(
                "SELECT access.approved_execution_origin(:member,:user,:event)"
                if origin
                else "SELECT access.approved_execution_context(:member,:user,:event)"
            ),
            dict(member=item.membership_id, user=item.user_id, event=item.event_id),
        ).scalar_one()
        source = model.model_validate(value)
    except ValidationError:
        raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "批准事件来源结构不完整") from None
    except DBAPIError as error:
        code = getattr(error.orig, "sqlstate", None)
        mappings = {
            "42501": ErrorCode.FORBIDDEN,
            "P0002": ErrorCode.NO_DATA,
            "22023": ErrorCode.IDEMPOTENCY_MISMATCH,
        }
        if code in mappings:
            raise ApiError(mappings[code], "批准事件原申请者、来源或当前身份不匹配") from None
        raise
    if source.message() != item:
        raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "消息不是原批准事件的完整信封")
    return source


def load_approved_event(session: Session, item: WorkMessage) -> ApprovedEvent:
    return _load(session, item, ApprovedEvent, origin=False)


def load_approved_origin(session: Session, item: WorkMessage) -> ApprovedOrigin:
    return _load(session, item, ApprovedOrigin, origin=True)
