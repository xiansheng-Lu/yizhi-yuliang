"""本人当前授权的追加式人工依据；无执行资格、资金或队列写入。"""

from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.execution.manual_evidence_models import ManualEvidenceInput, ManualEvidenceNote
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


def mapped(error: DBAPIError) -> ApiError:
    code = getattr(error.orig, "sqlstate", None)
    mapping = {
        "42501": ErrorCode.FORBIDDEN,
        "P0002": ErrorCode.NO_DATA,
        "23505": ErrorCode.IDEMPOTENCY_MISMATCH,
        "22023": ErrorCode.INVALID_INPUT,
        "54000": ErrorCode.DATA_COVERAGE_INSUFFICIENT,
    }
    if code not in mapping:
        raise error
    return ApiError(mapping[code], "人工依据权限、引用、内容或幂等键不满足要求")


class ExecutionManualEvidence:
    def __init__(self, engine: Engine, identity: VerifiedIdentity) -> None:
        self.engine, self.identity = engine, identity

    def append(self, job_id: UUID, value: ManualEvidenceInput) -> ManualEvidenceNote:
        try:
            # 即便调用方model_construct/model_copy也不得绕过输入/凭据样式校验。
            value = ManualEvidenceInput.model_validate(value.model_dump())
            with business_transaction(self.engine, self.identity) as session:
                row = session.execute(
                    text("""SELECT access.execution_manual_note_append(
                    :member,:user,:job,CAST(:input AS jsonb),:request)"""),
                    dict(
                        member=self.identity.membership_id,
                        user=self.identity.user_id,
                        job=job_id,
                        input=value.model_dump_json(),
                        request=uuid4(),
                    ),
                ).scalar_one()
                return ManualEvidenceNote.model_validate(row)
        except ValidationError:
            raise ApiError(
                ErrorCode.INVALID_INPUT, "人工依据结构或已识别凭据样式不被接受"
            ) from None
        except DBAPIError as error:
            raise mapped(error) from None

    def list(self, job_id: UUID) -> tuple[ManualEvidenceNote, ...]:
        engine = self.engine.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        try:
            with business_transaction(engine, self.identity) as session:
                rows = session.execute(
                    text("SELECT access.execution_manual_notes_read(:member,:user,:job)"),
                    dict(
                        member=self.identity.membership_id, user=self.identity.user_id, job=job_id
                    ),
                ).scalar_one()
                return tuple(ManualEvidenceNote.model_validate(row) for row in rows)
        except ValidationError:
            raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "人工依据历史结构不完整") from None
        except DBAPIError as error:
            raise mapped(error) from None
