"""数据库原子费用许可；可复用到模型/生成端口，默认无价格、无预算、无外部适配器。"""

import hashlib
import secrets
from collections.abc import Callable

from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.aikernel.cost_models import (
    BillableRequest,
    BillingOutcome,
    CallPermit,
    CostQuote,
)
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


def digest(token: SecretStr) -> str:
    return hashlib.sha256(token.get_secret_value().encode()).hexdigest()


class CostGate:
    def __init__(self, engine: Engine, identity: VerifiedIdentity) -> None:
        self.engine = engine
        self.identity = identity

    def quote(self, request: BillableRequest) -> CostQuote:
        with business_transaction(self.engine, self.identity) as session:
            return self.quote_in_transaction(session, request)

    def quote_in_transaction(self, session: Session, request: BillableRequest) -> CostQuote:
        row = (
            session.execute(
                text(
                    "SELECT * FROM access.ai_fee_quote(:member,:request,:kind,:scenario,"
                    ":price,:input,:output,:hash)"
                ),
                {
                    "member": self.identity.membership_id,
                    "request": request.request_key,
                    "kind": request.kind,
                    "scenario": request.scenario,
                    "price": request.price_key,
                    "input": request.input_units,
                    "output": request.output_units,
                    "hash": request.fingerprint(),
                },
            )
            .mappings()
            .one()
        )
        return CostQuote(**dict(row))

    def confirm(self, quote: CostQuote) -> None:
        with business_transaction(self.engine, self.identity) as session:
            return self.confirm_in_transaction(session, quote)

    def confirm_in_transaction(self, session: Session, quote: CostQuote) -> None:
        accepted = session.execute(
            text("SELECT access.ai_fee_confirm(:member,:quote,:hash,:maximum,:currency)"),
            {
                "member": self.identity.membership_id,
                "quote": quote.quote_id,
                "hash": quote.quote_hash,
                "maximum": quote.max_cost_minor,
                "currency": quote.currency,
            },
        ).scalar_one()
        if not accepted:
            raise ApiError(ErrorCode.VERSION_CONFLICT, "报价已变化或过期，请重新查看后确认")

    def acquire(self, quote: CostQuote) -> CallPermit:
        with business_transaction(self.engine, self.identity) as session:
            return self.acquire_in_transaction(session, quote)

    def acquire_in_transaction(self, session: Session, quote: CostQuote) -> CallPermit:
        """许可预占参加业务事务，失败时与调用方记录一起回滚。"""
        token = SecretStr(secrets.token_urlsafe(32))
        try:
            identifier = session.execute(
                text("SELECT access.ai_fee_acquire(:member,:quote,:token)"),
                {
                    "member": self.identity.membership_id,
                    "quote": quote.quote_id,
                    "token": digest(token),
                },
            ).scalar_one()
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "P0001":
                raise ApiError(ErrorCode.RISK_BLOCKED, "AI费用额度不足，未发起外部调用") from None
            raise
        return CallPermit(permit_id=identifier, request_key=quote.request_key, token=token)

    def start(self, permit: CallPermit, request: BillableRequest) -> None:
        with business_transaction(self.engine, self.identity) as session:
            self.start_in_transaction(session, permit, request)

    def start_in_transaction(
        self, session: Session, permit: CallPermit, request: BillableRequest
    ) -> None:
        if permit.request_key != request.request_key:
            raise ApiError(ErrorCode.FORBIDDEN, "费用许可不属于当前请求")
        accepted = session.execute(
            text("SELECT access.ai_fee_start(:member,:permit,:token,:hash)"),
            {
                "member": self.identity.membership_id,
                "permit": permit.permit_id,
                "token": digest(permit.token),
                "hash": request.fingerprint(),
            },
        ).scalar_one()
        if not accepted:
            raise ApiError(ErrorCode.RISK_BLOCKED, "费用许可不可用，未发起外部调用")

    def finish(self, permit: CallPermit, outcome: BillingOutcome) -> str:
        with business_transaction(self.engine, self.identity) as session:
            return self.finish_in_transaction(session, permit, outcome)

    def finish_in_transaction(
        self, session: Session, permit: CallPermit, outcome: BillingOutcome
    ) -> str:
        state = session.execute(
            text("SELECT access.ai_fee_finish(:permit,:token,:state,:amount,:currency,:proof)"),
            {
                "permit": permit.permit_id,
                "token": digest(permit.token),
                "state": outcome.state,
                "amount": outcome.amount_minor,
                "currency": outcome.currency,
                "proof": outcome.proof_hash,
            },
        ).scalar_one()
        return str(state)

    def cancel_unsent(self, permit: CallPermit) -> bool:
        with business_transaction(self.engine, self.identity) as session:
            return self.cancel_unsent_in_transaction(session, permit)

    def cancel_unsent_in_transaction(self, session: Session, permit: CallPermit) -> bool:
        return bool(
            session.execute(
                text("SELECT access.ai_fee_cancel(:permit,:token)"),
                {"permit": permit.permit_id, "token": digest(permit.token)},
            ).scalar_one()
        )

    def execute[T](
        self,
        permit: CallPermit,
        request: BillableRequest,
        call: Callable[[str], tuple[T, BillingOutcome]],
    ) -> T:
        # 许可先提交IN_FLIGHT，事务外调用；崩溃/异常不自动重试，不把未知成本当0释放。
        self.start(permit, request)
        try:
            result, outcome = call(str(request.request_key))
        except Exception:
            self._retain_unknown(permit)
            raise ApiError(ErrorCode.RESULT_UNKNOWN, "外部结果待核对，费用预占保留") from None
        try:
            self.finish(permit, outcome)
        except Exception:
            # 已发请求不能因记账失败重发。即便PG不可用，已提交的IN_FLIGHT仍持有预占。
            self._retain_unknown(permit)
            raise ApiError(ErrorCode.RESULT_UNKNOWN, "账单待核对，费用预占保留") from None
        return result

    def _retain_unknown(self, permit: CallPermit) -> None:
        try:
            self.finish(permit, BillingOutcome())
        except DBAPIError:
            # 不吞掉外部结果未知这一事实，也不把原始连接异常带到调用端。
            pass
