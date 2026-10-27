"""持久IN_FLIGHT + 同PG事务副作用/首次快照；不用于供应商写或网络重发。"""

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.connectors.redaction import canonical
from yuliang.modules.execution.guards import controlled
from yuliang.modules.execution.idempotency_authorization import IdempotencyAuthorization
from yuliang.modules.execution.idempotency_models import (
    ApiOperation,
    IdempotencyResult,
    OwnershipTicket,
    ResponseSnapshot,
)
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import STATUS, ApiError, ErrorCode


def in_flight_control[T](operation: Callable[[], T]) -> T:
    try:
        return controlled(operation)
    except ApiError as error:
        if error.code == ErrorCode.LOCKED:
            raise ApiError(
                ErrorCode.LOCKED,
                "原请求仍在处理，请按同一键查询或恢复",
                details=[{"field": "idempotency", "reason": "IN_FLIGHT"}],
                retry_after=1,
            ) from None
        raise


class IdempotencyLedger:
    def __init__(
        self,
        engine: Engine,
        identity: VerifiedIdentity,
        request_id: UUID,
        authorization: IdempotencyAuthorization | None = None,
        *,
        lease_seconds: int = 30,
    ) -> None:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("幂等处理窗口需1至300秒")
        self.engine, self.identity, self.request_id, self.authorization = (
            engine,
            identity,
            request_id,
            authorization,
        )
        self.seconds = lease_seconds

    def _authorizer(self) -> IdempotencyAuthorization:
        if self.authorization is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "幂等操作尚未装配当前范围授权")
        return self.authorization

    def _key(self, operation: ApiOperation, key: str) -> str:
        if (
            not isinstance(operation, ApiOperation)
            or not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key)
        ):
            raise ApiError(ErrorCode.INVALID_INPUT, "需要明确操作与有效幂等键")
        # 键不授予权限；稳定SHA避免审计密钥轮换后旧键被误当新请求。
        return hashlib.sha256(key.encode()).hexdigest()

    def _payload(self, operation: ApiOperation, payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            canonical(
                [self.identity.tenant_id, str(self.identity.user_id), operation.value, payload]
            )
        ).hexdigest()

    def _find(
        self, session: Session, operation: ApiOperation, digest: str
    ) -> dict[str, Any] | None:
        value = (
            session.execute(
                text("""SELECT * FROM api_idempotency WHERE tenant_id=:tenant
            AND caller_id=:caller AND operation=:operation AND key_digest=:key"""),
                {
                    "tenant": self.identity.tenant_id,
                    "caller": self.identity.user_id,
                    "operation": operation.value,
                    "key": digest,
                },
            )
            .mappings()
            .first()
        )
        return dict(value) if value is not None else None

    def _audit(self, session: Session, action: str, identity: UUID) -> None:
        record(
            session,
            self.identity,
            {
                "action": action,
                "object_type": "idempotency_request",
                "object_id": identity,
                "request_id": self.request_id,
            },
        )

    def _result(self, row: dict[str, Any], owner: SecretStr | None = None) -> IdempotencyResult:
        identity = UUID(str(row["id"]))
        complete = row["state"] == "COMPLETED"
        return IdempotencyResult(
            record_id=identity,
            state="COMPLETED" if complete else "ACQUIRED" if owner else "IN_FLIGHT",
            request_id=row["request_id"],
            lease_until=row["lease_until"],
            ticket=OwnershipTicket(record_id=identity, secret=owner)
            if owner and not complete
            else None,
            response=ResponseSnapshot(status=row["status_code"], body=row["response_body"])
            if complete
            else None,
        )

    def begin(
        self, operation: ApiOperation, key: str, payload: dict[str, Any]
    ) -> IdempotencyResult:
        authorizer = self._authorizer()
        digest, payload_digest = self._key(operation, key), self._payload(operation, payload)
        with business_transaction(self.engine, self.identity) as session:
            row = self._find(session, operation, digest)
            owner = None
            if row is None:
                reference = authorizer.reference(session, operation, payload)
                candidate = SecretStr(secrets.token_urlsafe(32))
                stamp = session.execute(text("SELECT clock_timestamp()")).scalar_one()
                created = session.execute(
                    text("""INSERT INTO api_idempotency(id,tenant_id,caller_id,
                    membership_id,operation,key_digest,payload_digest,authorization_ref,state,
                    owner_digest,lease_until,request_id)
                    VALUES(:id,:tenant,:caller,:member,:operation,
                    :key,:payload,CAST(:reference AS jsonb),'IN_FLIGHT',:owner,:until,:request)
                    ON CONFLICT(tenant_id,caller_id,operation,key_digest)
                    DO NOTHING RETURNING id"""),
                    {
                        "id": uuid4(),
                        "tenant": self.identity.tenant_id,
                        "caller": self.identity.user_id,
                        "member": self.identity.membership_id,
                        "operation": operation.value,
                        "key": digest,
                        "payload": payload_digest,
                        "reference": json.dumps(reference),
                        "owner": hashlib.sha256(candidate.get_secret_value().encode()).hexdigest(),
                        "until": stamp + timedelta(seconds=self.seconds),
                        "request": self.request_id,
                    },
                ).scalar_one_or_none()
                row = self._find(session, operation, digest)
                if created is not None:
                    owner = candidate
                    self._audit(session, "idempotency.start", created)
            if row is None:
                raise RuntimeError("幂等登记未持久化")
            authorizer.check(session, operation, row["authorization_ref"])
            if not hmac.compare_digest(row["payload_digest"], payload_digest):
                raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "同一操作键已绑定其他载荷")
            return self._result(row, owner)

    def lookup(self, operation: ApiOperation, key: str) -> IdempotencyResult:
        digest = self._key(operation, key)
        with business_transaction(self.engine, self.identity) as session:
            row = self._find(session, operation, digest)
            if row is None:
                raise ApiError(ErrorCode.NO_DATA, "当前调用者没有此操作键")
            self._authorizer().check(session, operation, row["authorization_ref"])
            return self._result(row)

    def execute(
        self, ticket: OwnershipTicket, handler: Callable[[Session], ResponseSnapshot]
    ) -> ResponseSnapshot:
        """handler只能执行本PG同事务业务；外部IO必须改用execution attempt，不能放进此回调。"""
        with business_transaction(self.engine, self.identity) as session:
            row = in_flight_control(
                lambda: session.execute(
                    text("SELECT access.idempotency_lock(:member,:user,:id,:secret)"),
                    {
                        "member": self.identity.membership_id,
                        "user": self.identity.user_id,
                        "id": ticket.record_id,
                        "secret": ticket.secret.get_secret_value(),
                    },
                ).scalar_one()
            )
            self._authorizer().check(
                session, ApiOperation(row["operation"]), row["authorization_ref"]
            )
            if row["state"] == "COMPLETED":
                return ResponseSnapshot(status=row["status_code"], body=row["response_body"])
            try:
                with session.begin_nested():
                    response = ResponseSnapshot.model_validate(handler(session))
            except ApiError as error:
                if not 400 <= STATUS[error.code] < 500:
                    raise
                response = ResponseSnapshot(
                    status=STATUS[error.code],
                    body={
                        "error": {
                            "code": error.code.value,
                            "message": error.message,
                            "details": error.details,
                            "retryable": False,
                        }
                    },
                )
            in_flight_control(
                lambda: session.execute(
                    text("""SELECT access.idempotency_finish(
                :member,:user,:id,:secret,:status,CAST(:body AS jsonb))"""),
                    {
                        "member": self.identity.membership_id,
                        "user": self.identity.user_id,
                        "id": ticket.record_id,
                        "secret": ticket.secret.get_secret_value(),
                        "status": response.status,
                        "body": json.dumps(response.body),
                    },
                ).scalar_one()
            )
            self._audit(session, "idempotency.complete", ticket.record_id)
            return response

    def recover(self, operation: ApiOperation, key: str) -> IdempotencyResult:
        digest = self._key(operation, key)
        with business_transaction(self.engine, self.identity) as session:
            row = self._find(session, operation, digest)
            if row is None:
                raise ApiError(ErrorCode.NO_DATA, "当前调用者没有此操作键")
            self._authorizer().check(session, operation, row["authorization_ref"])
            owner = SecretStr(secrets.token_urlsafe(32))
            recovered = in_flight_control(
                lambda: session.execute(
                    text("""SELECT access.idempotency_recover(
                :member,:user,:id,:secret,:seconds)"""),
                    {
                        "member": self.identity.membership_id,
                        "user": self.identity.user_id,
                        "id": row["id"],
                        "secret": owner.get_secret_value(),
                        "seconds": self.seconds,
                    },
                ).scalar_one()
            )
            if recovered["state"] == "IN_FLIGHT":
                self._audit(session, "idempotency.recover", UUID(recovered["id"]))
            return self._result(recovered, owner)
