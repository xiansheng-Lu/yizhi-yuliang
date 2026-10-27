"""只改持有有效租约的连接器调度单，不改变命令执行单状态。"""

from datetime import timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.governance import Admission
from yuliang.platform.db import VerifiedIdentity


def reschedule_connector_task(
    session: Session,
    identity: VerifiedIdentity,
    task_id: UUID,
    lease_owner: str,
    decision: Admission,
) -> Literal["RESCHEDULED", "FAILED"]:
    if decision.allowed or decision.scope.ctx.identity != identity:
        raise ValueError("只能延期本人的被拒绝调用")
    row = (
        session.execute(
            text("""
        SELECT *,clock_timestamp() AS db_now FROM scheduled_tasks
        WHERE id=:id AND tenant_id=:tenant AND kind LIKE 'connector.%'
          AND state='CLAIMED' AND lease_owner=:owner AND lease_until>clock_timestamp()
        FOR UPDATE
    """),
            {"id": task_id, "tenant": identity.tenant_id, "owner": lease_owner},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ValueError("调度租约不匹配、已过期或任务不属于连接器")
    retry = decision.retry_at
    state: Literal["RESCHEDULED", "FAILED"] = "RESCHEDULED"
    if retry is None or row["attempts"] >= row["max_attempts"]:
        state = "FAILED"
    due = max(retry, row["db_now"] + timedelta(seconds=1)) if retry else row["db_now"]
    updated = session.execute(
        text("""UPDATE scheduled_tasks SET state=:state,due_at=:due,
        lease_owner=NULL,lease_until=NULL,last_error=:reason
        WHERE id=:id AND state='CLAIMED' AND lease_owner=:owner
          AND lease_until>clock_timestamp() RETURNING id"""),
        {
            "id": task_id,
            "state": state,
            "due": due,
            "reason": "CONNECTOR_" + decision.reason,
            "owner": lease_owner,
        },
    ).scalar_one_or_none()
    if updated is None:
        raise ValueError("更新前调度租约已失效")
    return state
