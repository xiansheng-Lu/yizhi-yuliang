"""代码显式装配的执行器；默认空，不从消息/环境加载URL或任意工厂。"""

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.base import GatewayContext, ObjectRef
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.execution.approval_outbox import ApprovedEvent
from yuliang.modules.execution.attempts import CommandSerializer
from yuliang.platform.db import VerifiedIdentity


@dataclass(frozen=True)
class DispatchExecutor:
    ctx: GatewayContext
    serializer: CommandSerializer = field(repr=False)
    client: RecordedPlatformClient | GovernedPlatformClient = field(repr=False)
    credential: Callable[[], SecretStr] = field(repr=False)
    target: ObjectRef


ExecutorFactory = Callable[[Session, VerifiedIdentity, ApprovedEvent], DispatchExecutor]


class DispatchRuntime:
    def __init__(self) -> None:
        self._factories: dict[tuple[str, str, str], ExecutorFactory] = {}

    def register(
        self, action: str, provider: str, product_line: str, factory: ExecutorFactory
    ) -> None:
        key = (action, provider, product_line)
        if key in self._factories or not callable(factory):
            raise ValueError("执行器须显式装配且不可覆盖")
        self._factories[key] = factory

    def resolve(self, session: Session, event: ApprovedEvent) -> DispatchExecutor | None:
        row = session.execute(
            text("""SELECT j.document->>'action_type',a.provider,a.product_line
          FROM command_jobs j JOIN ad_accounts a ON (a.tenant_id,a.id)=(j.tenant_id,j.account_id)
          WHERE j.id=:job AND j.created_by=:actor"""),
            dict(job=event.job_id, actor=event.subject_user_id),
        ).first()
        factory = self._factories.get(tuple(row)) if row is not None else None
        if factory is None:
            return None
        executor = factory(session, event.identity, event)
        if executor.ctx.identity != event.identity:
            raise ValueError("执行器上下文必须属于原申请创建者")
        return executor
