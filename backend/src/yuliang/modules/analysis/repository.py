"""可恢复实际阶段，租约fence与冻结结果写入走受控数据库函数。"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.analysis.calculator import calculate
from yuliang.modules.analysis.service import AnalysisService
from yuliang.modules.reporting.coverage import require_coverage
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError


class AnalysisLeaseBusy(RuntimeError):
    """原任务租约尚未到期；投递不能被提前ACK丢失恢复机会。"""


def execute_analysis(engine: Engine, identity: VerifiedIdentity, task_id: UUID) -> bool:
    consistent = engine.execution_options(isolation_level="REPEATABLE READ")

    def stage(number: int, token: UUID | None = None, result: str | None = None) -> UUID | None:
        with business_transaction(consistent, identity) as session:
            current = AnalysisService._task(session, task_id)
            if current.status in {"succeeded", "failed"}:
                return None
            original_scope = tuple(
                session.execute(
                    text("SELECT unnest(account_scope) FROM analysis_tasks WHERE id=:id"),
                    dict(id=task_id),
                ).scalars()
            )
            frozen = session.execute(
                text("SELECT snapshot IS NOT NULL FROM analysis_executions WHERE task_id=:id"),
                dict(id=task_id),
            ).scalar_one_or_none()
            if number in {10, 40} and not frozen:
                try:
                    require_coverage(session, current.query, account_scope=original_scope)
                except ApiError:
                    if number != 10:
                        raise
                    session.execute(
                        text("SELECT access.analysis_coverage_failed(:id)"), dict(id=task_id)
                    )
                    return None
            if number == 40:
                from yuliang.modules.analysis.comparisons import comparison_preflight

                if not frozen:
                    bundle = comparison_preflight(session, current.query, original_scope)
                    metadata = json.dumps(bundle)
                else:
                    metadata = None
                session.execute(
                    text(
                        "SELECT access.analysis_comparisons_freeze(:id,:token,CAST(:meta AS jsonb))"
                    ),
                    dict(id=task_id, token=token, meta=metadata),
                )
            return session.execute(
                text("SELECT access.analysis_execute(:id,:token,:stage,CAST(:result AS jsonb))"),
                dict(id=task_id, token=token, stage=number, result=result),
            ).scalar_one_or_none()

    token = stage(10)
    if token is None:
        with business_transaction(consistent, identity) as session:
            if AnalysisService._task(session, task_id).status == "running":
                raise AnalysisLeaseBusy("分析原租约尚未到期")
        return False
    try:
        stage(40, token)
        with business_transaction(consistent, identity) as session:
            task = AnalysisService._task(session, task_id)
            row = session.execute(
                text(
                    "SELECT snapshot,result_id,comparisons_snapshot FROM analysis_executions "
                    "WHERE task_id=:id"
                ),
                dict(id=task_id),
            ).one()
            name = session.execute(
                text("SELECT name FROM shops WHERE id=:id"), dict(id=task.query.shop_id)
            ).scalar_one()
            snapshot: list[dict[str, Any]] = row.snapshot
            accounts = set(
                session.execute(
                    text("SELECT unnest(account_scope) FROM analysis_tasks WHERE id=:id"),
                    dict(id=task_id),
                ).scalars()
            )
        result = calculate(task.query, snapshot, task_id, row.result_id, name)
        from yuliang.modules.analysis.comparisons import apply_comparisons

        result = apply_comparisons(result, row.comparisons_snapshot or {})
        from yuliang.modules.analysis.narrative import rule_insights

        result = result.model_copy(update=dict(insights=rule_insights(result, snapshot, accounts)))
        stage(75, token)
        payload = result.model_dump_json(by_alias=True)
        # JSON可序列化与冻结模型校验已实际完成才推进90。
        json.loads(payload)
        stage(90, token)
        stage(100, token, payload)
        return True
    except DBAPIError:
        raise
    except Exception:  # 安全固定错误，内部异常与载荷不对外输出。
        stage(-1, token)
        return False
