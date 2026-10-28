"""受理同事务写任务与Outbox；历史只读，轮询不驱动任何计算或阶段。"""

from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.analysis.models import AnalysisCreate, AnalysisTask
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class AnalysisService:
    def __init__(self, engine: Engine, identity: VerifiedIdentity) -> None:
        self.engine, self.identity = engine, identity

    @staticmethod
    def _task(session: Session, identifier: UUID) -> AnalysisTask:
        row = (
            session.execute(text("SELECT * FROM analysis_tasks WHERE id=:id"), {"id": identifier})
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "分析任务不存在或当前范围不可见")
        return AnalysisTask(
            id=row["id"],
            query=row["query_json"],
            status=row["status"],
            progress=row["progress"],
            result_id=row["result_id"],
            error=row["error"],
            created_at=row["created_at"],
        )

    def create(self, value: AnalysisCreate) -> AnalysisTask:
        try:
            with business_transaction(self.engine, self.identity) as session:
                identifier = session.execute(
                    text("SELECT access.analysis_admit(CAST(:query AS jsonb),:key)"),
                    {
                        "query": value.query.model_dump_json(by_alias=True),
                        "key": value.idempotency_key,
                    },
                ).scalar_one()
                return self._task(session, identifier)
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None)
            if code == "42501":
                raise ApiError(ErrorCode.FORBIDDEN, "没有当前分析条件的操作或数据范围") from None
            if code == "P0409":
                raise ApiError(
                    ErrorCode.IDEMPOTENCY_MISMATCH, "幂等键已绑定另一组分析条件"
                ) from None
            if code in {"22023", "22007", "22008", "22P02"}:
                raise ApiError(ErrorCode.INVALID_INPUT, "分析条件不合法") from None
            raise

    def get(self, identifier: UUID) -> AnalysisTask:
        with business_transaction(self.engine, self.identity) as session:
            return self._task(session, identifier)

    def tasks(self) -> tuple[AnalysisTask, ...]:
        with business_transaction(self.engine, self.identity) as session:
            identifiers = (
                session.execute(
                    text("SELECT id FROM analysis_tasks ORDER BY created_at DESC,id LIMIT 1001")
                )
                .scalars()
                .all()
            )
            if len(identifiers) > 1000:
                raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "分析历史过大，需先收窄范围")
            return tuple(self._task(session, identifier) for identifier in identifiers)

    def results(self, identifier: UUID | None = None) -> list[dict[str, Any]]:
        with business_transaction(self.engine, self.identity) as session:
            rows = (
                session.execute(
                    text(
                        "SELECT payload FROM analysis_results "
                        "WHERE (CAST(:id AS uuid) IS NULL OR id=:id) "
                        "ORDER BY created_at DESC,id LIMIT 1001"
                    ),
                    {"id": identifier},
                )
                .scalars()
                .all()
            )
            if identifier is not None and not rows:
                raise ApiError(ErrorCode.NO_DATA, "分析结果不存在或当前范围不可见")
            if len(rows) > 1000:
                raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "结果历史过大，需先收窄范围")
            return [dict(row) for row in rows]
