"""原出站键只查档；新请求先准入，调度层接管退避，不在HTTP层重试。"""

from typing import Any, Literal, cast
from uuid import UUID

from pydantic import SecretStr

from yuliang.modules.connectors.archive import RecordedPlatformClient, RecordedResult
from yuliang.modules.connectors.base import GatewayContext, ObjectRef
from yuliang.modules.connectors.governance import (
    Admission,
    CallDeferred,
    GovernanceUnavailable,
    Lane,
    PlatformGovernance,
    QuotaScope,
)
from yuliang.modules.connectors.http import OperationSpec
from yuliang.modules.connectors.outcomes import Outcome


class GovernedPlatformClient:
    def __init__(
        self, client: RecordedPlatformClient, governance: PlatformGovernance, application_id: UUID
    ) -> None:
        self.client, self.governance, self.application_id = client, governance, application_id

    async def send(
        self,
        ctx: GatewayContext,
        spec: OperationSpec,
        outbound_id: UUID,
        parameters: dict[str, Any],
        credential: SecretStr,
        *,
        lane: Lane,
        target: ObjectRef | None = None,
        execution_id: UUID | None = None,
        attempt_id: UUID | None = None,
    ) -> RecordedResult:
        if spec.max_read_attempts != 1:
            raise ValueError("受控调用每次只能有一个网络尝试，退避交给持久调度")
        if (lane == "EXECUTE") != (spec.effect == "WRITE") or spec.effect not in {"READ", "WRITE"}:
            raise ValueError("业务通道必须匹配操作类型；探测只能只读")
        ticket: Admission | None = None

        def acquire() -> None:
            nonlocal ticket
            ticket = self.governance.admit(QuotaScope(self.application_id, ctx), lane)
            if not ticket.allowed:
                raise CallDeferred(ticket)

        result = await self.client.send(
            ctx,
            spec,
            outbound_id,
            parameters,
            credential,
            target=target,
            execution_id=execution_id,
            attempt_id=attempt_id,
            before_create=acquire,
        )
        if ticket is None:
            return result  # 原档案不再消耗配额，也不重复报告旧结果。
        metadata = dict(result.metadata)
        outcome = Outcome(
            str(metadata["category"]),
            cast(
                Literal[
                    "ACCEPTED", "REJECTED", "UNKNOWN", "NOT_SENT", "READ_SUCCEEDED", "READ_FAILED"
                ],
                metadata["externalOutcome"],
            ),
            retry_after_seconds=metadata["retryAfterSeconds"],
            retry_after_unconfirmed=metadata["retryAfterUnconfirmed"],
        )
        try:
            self.governance.report(ticket, outcome)
            metadata["governanceRecorded"] = True
        except GovernanceUnavailable:
            # 平台响应已在PG保存，不把准入设施故障改写为平台未知/失败。
            metadata["governanceRecorded"] = False
        return RecordedResult(metadata, result.exchange, result.error)
