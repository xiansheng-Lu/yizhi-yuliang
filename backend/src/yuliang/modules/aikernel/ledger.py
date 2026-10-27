"""调用前持久STARTED，完成事实通过受限CAS函数写入；不存提示输入或API密钥。"""

import time
from typing import Any

from sqlalchemy import Engine, text

from yuliang.modules.aikernel.models import Candidate, Completion, InvocationRequest
from yuliang.platform.db import VerifiedIdentity, business_transaction


class InvocationLedger:
    def __init__(self, engine: Engine, identity: VerifiedIdentity) -> None:
        self.engine = engine
        self.identity = identity

    def begin(self, request: InvocationRequest) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as session:
            row = (
                session.execute(
                    text("""
              SELECT * FROM access.ai_begin(:call,:request,:member,:user,:scenario,:alias,
                :prompt,:version,:role,:parent)
            """),
                    {
                        "call": request.call_id,
                        "request": request.request_id,
                        "member": self.identity.membership_id,
                        "user": self.identity.user_id,
                        "scenario": request.scenario,
                        "alias": request.capability_alias,
                        "prompt": request.prompt_key,
                        "version": request.prompt_version,
                        "role": request.model_role,
                        "parent": request.parent_call_id,
                    },
                )
                .mappings()
                .one()
            )
            return dict(row)

    def finish(self, request: InvocationRequest, outcome: Completion) -> None:
        usage = outcome.usage
        with business_transaction(self.engine, self.identity) as session:
            changed = session.execute(
                text("""
              SELECT access.ai_finish(:call,:user,:status,:usage,:cost,:input,:output,
                :amount,:latency,:external,:dispatch,:currency,:provider_model,:response_hash)
            """),
                {
                    "call": request.call_id,
                    "user": self.identity.user_id,
                    "status": outcome.status,
                    "usage": usage.usage_state,
                    "cost": usage.cost_state,
                    "input": usage.tokens_in,
                    "output": usage.tokens_out,
                    "amount": usage.cost_minor,
                    "latency": outcome.latency_ms,
                    "external": outcome.external_request_id,
                    "dispatch": outcome.dispatch_state,
                    "currency": usage.cost_currency,
                    "provider_model": outcome.provider_model,
                    "response_hash": outcome.response_hash,
                },
            ).scalar_one()
        if not changed:
            raise ValueError("调用已结束或不在当前范围，不能覆盖用量与费用")


class UnavailableGateway:
    """M2-01a只交付网关协议。供应商适配/出站/限额门禁就绪前不发送请求。"""

    def __init__(self, ledger: InvocationLedger) -> None:
        self.ledger = ledger

    def generate(self, request: InvocationRequest) -> Candidate:
        started = time.monotonic()
        self.ledger.begin(request)
        self.ledger.finish(
            request,
            Completion(
                status="FALLBACK",
                dispatch_state="NOT_SENT",
                latency_ms=int((time.monotonic() - started) * 1000),
            ),
        )
        return Candidate(call_id=request.call_id, source="RULES", status="UNAVAILABLE")
