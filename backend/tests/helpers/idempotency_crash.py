"""集成子进程在业务写后退出；凭据仅经测试进程环境输入，不输出环境或请求。"""

import json
import os
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import CreateJobRequest
from yuliang.modules.execution.idempotency import IdempotencyLedger
from yuliang.modules.execution.idempotency_authorization import CommandAuthorization
from yuliang.modules.execution.idempotency_models import OwnershipTicket, ResponseSnapshot
from yuliang.platform.db import VerifiedIdentity


def main() -> None:
    engine = create_engine(os.environ["YULIANG_IDEM_PROBE_DB_URL"], hide_parameters=True)
    raw = json.loads(os.environ["YULIANG_IDEM_PROBE_IDENTITY"])
    identity = VerifiedIdentity(raw["tenant"], UUID(raw["member"]), UUID(raw["user"]))
    apps = JobApplications(
        engine, identity, uuid4(), SecretStr(os.environ["YULIANG_IDEM_PROBE_AUDIT_KEY"])
    )
    ledger = IdempotencyLedger(engine, identity, uuid4(), CommandAuthorization(apps))
    value = CreateJobRequest.model_validate_json(os.environ["YULIANG_IDEM_PROBE_PAYLOAD"])
    ticket = OwnershipTicket(
        record_id=UUID(os.environ["YULIANG_IDEM_PROBE_RECORD"]),
        secret=SecretStr(os.environ["YULIANG_IDEM_PROBE_OWNER"]),
    )

    def interrupted(session: Session) -> ResponseSnapshot:
        apps.create(value, unit_of_work=session)
        print("BUSINESS_WRITTEN_UNCOMMITTED", flush=True)
        os._exit(17)  # 真实进程退出，故意绕过Python上下文清理，PG连接关闭后回滚。

    ledger.execute(ticket, interrupted)


if __name__ == "__main__":
    main()
