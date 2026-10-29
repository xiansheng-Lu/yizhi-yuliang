"""先持久SENT再事务外一次submit；不明结果仅query原键。"""

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.aikernel.cost_gate import CostGate
from yuliang.modules.content.generation_models import GenerationCreate, GenerationInput
from yuliang.modules.content.generation_provider import (
    GenerationProvider,
    ProviderResult,
    UnavailableGenerationProvider,
)
from yuliang.modules.content.generation_repository import GenerationRepository
from yuliang.modules.content.models import Document
from yuliang.modules.content.service import ContentService
from yuliang.modules.content.validation import validate_content
from yuliang.modules.content.workflows_execution_service import WorkflowExecution
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class GenerationService:
    def __init__(self, content: ContentService, provider: GenerationProvider | None = None) -> None:
        self.content = content
        self.repo = GenerationRepository(content)
        self.gate = CostGate(content.engine, content.identity)
        self.provider = provider or UnavailableGenerationProvider()

    def _prepared(self, value: GenerationInput) -> tuple[Any, Any, Document, Any]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            run, step, doc = self.repo.input(s, value)
        proofs = self.repo.assets.prepare_document(doc)
        return run, step, doc, proofs

    def quote(self, value: GenerationInput) -> dict[str, Any]:
        value = GenerationInput.model_validate(value.model_dump())
        _, _, _, proofs = self._prepared(value)
        with business_transaction(self.content.engine, self.content.identity) as s:
            self.repo.input(s, value)
            request, _ = self.repo.request(s, value, proofs)
        quote = self.gate.quote(request)
        with business_transaction(self.content.engine, self.content.identity) as s:
            self.repo.quote(s, quote.quote_id, request, value.provider, value.kind)
        return quote.model_dump(mode="json")

    def confirm(self, value: GenerationCreate) -> None:
        value = GenerationCreate.model_validate(value.model_dump())
        _, _, _, proofs = self._prepared(value.input)
        with business_transaction(self.content.engine, self.content.identity) as s:
            request, _ = self.repo.request(s, value.input, proofs)
            quote = self.repo.quote(
                s, UUID(value.quoteId), request, value.input.provider, value.input.kind
            )
        if (quote.quote_hash, quote.max_cost_minor, quote.currency) != (
            value.quoteHash,
            value.maximumMinor,
            value.currency,
        ):
            raise ApiError(ErrorCode.VERSION_CONFLICT, "生成报价已变化，请重新查看确认")
        self.gate.confirm(quote)

    def create(self, value: GenerationCreate) -> dict[str, Any]:
        value = GenerationCreate.model_validate(value.model_dump())
        key = UUID(value.input.requestKey)
        with business_transaction(self.content.engine, self.content.identity) as s:
            old = s.execute(
                text("SELECT id,client_hash FROM generation_jobs WHERE request_key=:key"),
                dict(key=key),
            ).first()
            if old:
                row = self.repo.job(s, old.id)
                if old.client_hash != value.input.digest():
                    raise ApiError(ErrorCode.VERSION_CONFLICT, "原生成键已用于其他载荷")
                return self.repo.view(row)
        _, _, _, proofs = self._prepared(value.input)
        try:
            with business_transaction(self.content.engine, self.content.identity) as s:
                s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760170))"),
                    dict(key=f"{self.content.identity.tenant_id}|{key}"),
                )
                old = s.execute(
                    text("SELECT id,client_hash FROM generation_jobs WHERE request_key=:key"),
                    dict(key=key),
                ).first()
                if old:
                    row = self.repo.job(s, old.id)
                    if old.client_hash != value.input.digest():
                        raise ApiError(ErrorCode.VERSION_CONFLICT, "原生成键已用于其他载荷")
                    return self.repo.view(row)
                run, step, doc = self.repo.input(s, value.input, lock=True)
                self.repo.assets.validate_document(s, doc, proofs)
                request, snapshot = self.repo.request(s, value.input, proofs)
                quote = self.repo.quote(
                    s,
                    UUID(value.quoteId),
                    request,
                    value.input.provider,
                    value.input.kind,
                    confirmed=True,
                )
                if (quote.quote_hash, quote.max_cost_minor, quote.currency) != (
                    value.quoteHash,
                    value.maximumMinor,
                    value.currency,
                ):
                    raise ApiError(ErrorCode.VERSION_CONFLICT, "报价确认不匹配")
                permit = self.gate.acquire_in_transaction(s, quote)
                identifier = uuid4()
                args = dict(
                    id=identifier,
                    tenant=self.content.identity.tenant_id,
                    actor=self.content.identity.user_id,
                    member=self.content.identity.membership_id,
                    shop=run["shop_id"],
                    product=run["product_id"],
                    document=UUID(doc.id),
                    docversion=doc.version,
                    run=run["id"],
                    step=step["id"],
                    attempt=step["attempt"],
                    runversion=run["revision"],
                    provider=value.input.provider,
                    kind=value.input.kind,
                    key=key,
                    client=value.input.digest(),
                    payload=request.payload_hash,
                    fingerprint=request.fingerprint(),
                    quote=quote.quote_id,
                    permit=permit.permit_id,
                    parameters=json.dumps(value.input.model_dump()),
                    assets=json.dumps(snapshot["assets"]),
                    estimated=quote.max_cost_minor,
                    currency=quote.currency,
                )
                s.execute(
                    text("""INSERT INTO generation_jobs(id,tenant_id,actor_id,membership_id,shop_id,
                    product_id,document_id,document_revision,run_id,step_id,step_attempt,expected_run_revision,
                    provider,kind,request_key,client_hash,payload_hash,request_fingerprint,quote_id,permit_id,
                    status,submit_state,parameters,assets_snapshot,estimated_cost_minor,currency)
                    VALUES(:id,:tenant,:actor,:member,:shop,:product,:document,:docversion,:run,:step,:attempt,
                    :runversion,:provider,:kind,:key,:client,:payload,:fingerprint,:quote,:permit,
                    'QUEUED','NOT_SENT',CAST(:parameters AS jsonb),
                    CAST(:assets AS jsonb),:estimated,:currency)"""),
                    args,
                )
                for asset, proof in proofs.items():
                    self.repo.assets.reference_in_transaction(
                        s, asset, "GENERATION", identifier, proof=proof
                    )
                s.execute(
                    text(
                        "UPDATE workflow_run_steps SET status='RUNNING',document_id=:document "
                        "WHERE id=:step"
                    ),
                    args,
                )
                s.execute(
                    text(
                        "UPDATE workflow_runs SET revision=revision+1,updated_at=clock_timestamp() "
                        "WHERE id=:run"
                    ),
                    args,
                )
                return self.repo.view(self.repo.job(s, identifier))
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise ApiError(
                    ErrorCode.VERSION_CONFLICT, "生成键冲突或此run-step已有在途生成"
                ) from None
            raise

    def get(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            return self.repo.view(self.repo.job(s, identifier))

    def _dispatch_scope(self, s: Any, row: Any) -> Document:
        run, steps, _ = WorkflowExecution(self.content)._load(s, row["run_id"], True)
        step = next((v for v in steps if v["id"] == row["step_id"]), None)
        document = self.content._row(s, row["document_id"], True)
        if (
            run["status"] != "RUNNING"
            or step is None
            or step["status"] != "RUNNING"
            or step["attempt"] != row["step_attempt"]
            or document["revision"] != row["document_revision"]
        ):
            raise ApiError(ErrorCode.VERSION_CONFLICT, "生成输入/步骤已变化，未发送")
        return validate_content(document["payload"], purpose="GENERATION")

    def _ensure_dispatchable(self) -> None:
        # 默认不可用在任何SENT/费用消费前失败，不能假调用后报成功。
        if isinstance(self.provider, UnavailableGenerationProvider):
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "真实供应商默认关闭；未发送")

    def _before_send(self, s: Any, row: Any, identifier: UUID) -> None:
        """发送栅栏内钩子：视频编排在此同一事务写入任务账本，默认无。"""

    def _send(self, identifier: UUID, frozen: dict[str, Any]) -> ProviderResult:
        return self.provider.submit(
            str(frozen["request_key"]), frozen["payload_hash"], frozen["parameters"]
        )

    def dispatch(self, identifier: UUID) -> dict[str, Any]:
        self._ensure_dispatchable()
        with business_transaction(self.content.engine, self.content.identity) as s:
            row = self.repo.job(s, identifier)
            if row["submit_state"] != "NOT_SENT":
                return self.repo.view(row)
            document = self._dispatch_scope(s, row)
        proofs = self.repo.assets.prepare_document(document)
        with business_transaction(self.content.engine, self.content.identity) as s:
            row = self.repo.job(s, identifier, lock=True)
            if row["submit_state"] != "NOT_SENT":
                return self.repo.view(row)
            doc = self._dispatch_scope(s, row)
            self.repo.assets.validate_document(s, doc, proofs)
            request, snapshot = self.repo.request(
                s, GenerationInput.model_validate(row["parameters"]), proofs
            )
            if (
                request.fingerprint() != row["request_fingerprint"]
                or snapshot["assets"] != row["assets_snapshot"]
            ):
                raise ApiError(ErrorCode.VERSION_CONFLICT, "固定生成输入或素材已变化")
            allowed = s.execute(
                text("SELECT access.generation_fee_start(:job)"), dict(job=identifier)
            ).scalar_one()
            if not allowed:
                raise ApiError(ErrorCode.RISK_BLOCKED, "生成报价/许可不可用，未发送")
            s.execute(
                text("""INSERT INTO generation_dispatches(tenant_id,job_id,attempt_no,
                request_key,payload_hash)
                VALUES(:tenant,:job,1,:key,:hash)"""),
                dict(
                    tenant=self.content.identity.tenant_id,
                    job=identifier,
                    key=row["request_key"],
                    hash=row["payload_hash"],
                ),
            )
            s.execute(
                text("""UPDATE generation_jobs SET status='RUNNING',submit_state='SENT',
                version=version+1,updated_at=clock_timestamp() WHERE id=:job"""),
                dict(job=identifier),
            )
            self._before_send(s, row, identifier)
            frozen = dict(row)
        try:
            result = self._send(identifier, frozen)
        except Exception:
            result = ProviderResult("UNKNOWN")
        return self._record(identifier, result)

    def query(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            row = self.repo.job(s, identifier)
            terminal = row["status"] in {"SUCCEEDED", "FAILED", "WAITING_REVIEW"}
            if (
                row["submit_state"] == "NOT_SENT"
                or row["status"] == "CANCELLED"
                or terminal
                and row["billing_state"] == "KNOWN"
            ):
                return self.repo.view(row)
            key = str(row["request_key"])
        return self._record(identifier, self.provider.query(key))

    def _lock_assets(self, s: Any, row: Any, extra: UUID | None = None) -> None:
        identifiers = {UUID(value) for value in row["assets_snapshot"]}
        if extra is not None:
            identifiers.add(extra)
        for identifier in sorted(identifiers, key=str):
            self.repo.assets._lock(s, identifier)

    def _record(self, identifier: UUID, result: ProviderResult) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            preview = self.repo.job(s, identifier)
            only_billing = preview["status"] in {
                "SUCCEEDED",
                "FAILED",
                "WAITING_REVIEW",
                "CANCELLED",
            }
        asset_proof = (
            self.repo.assets.prepare_reference(result.asset_id)
            if result.asset_id and not only_billing
            else None
        )
        with business_transaction(self.content.engine, self.content.identity) as s:
            row = self.repo.job(s, identifier, lock=True)
            self._lock_assets(s, row, result.asset_id)
            if row["billing_state"] == "KNOWN" and result.billing.state == "KNOWN":
                proof = s.execute(
                    text("SELECT proof_hash FROM ai_cost_permits WHERE id=:id"),
                    dict(id=row["permit_id"]),
                ).scalar_one()
                if (row["actual_cost_minor"], row["currency"], proof) != (
                    result.billing.amount_minor,
                    result.billing.currency,
                    result.billing.proof_hash,
                ):
                    raise ApiError(
                        ErrorCode.VERSION_CONFLICT, "新费用回执与已结算事实冲突，不覆盖旧账本"
                    )
            if row["status"] == "CANCELLED":
                return self.repo.view(row)
            terminal = row["status"] in {"SUCCEEDED", "FAILED", "WAITING_REVIEW"}
            if terminal and row["billing_state"] == "KNOWN":
                return self.repo.view(row)
            status = row["status"] if terminal else "WAITING_PROVIDER"
            asset = row["result_asset_id"]
            submit = "UNKNOWN" if result.state == "UNKNOWN" else "ACKNOWLEDGED"
            if not terminal and result.state == "READY_FOR_REVIEW":
                if asset_proof is None or result.asset_id is None:
                    raise ApiError(
                        ErrorCode.CAPABILITY_UNAVAILABLE, "产物未实际入私有素材，保持待核对"
                    )
                if asset_proof.product != row["product_id"]:
                    raise ApiError(ErrorCode.FORBIDDEN, "生成产物商品范围不符")
                self.repo.assets.reference_in_transaction(
                    s, result.asset_id, "GENERATION", identifier, proof=asset_proof
                )
                asset = result.asset_id
                status = "WAITING_REVIEW"
            elif not terminal and result.state == "FAILED":
                status = "FAILED"
            if (
                row["external_job_id"]
                and result.external_id
                and row["external_job_id"] != result.external_id
            ):
                raise ApiError(ErrorCode.RESULT_UNKNOWN, "同供应商请求键返回冲突外部ID")
            if row["submit_state"] == "ACKNOWLEDGED" and submit == "UNKNOWN":
                submit = "ACKNOWLEDGED"
            outcome = result.billing
            if row["billing_state"] == "KNOWN":
                # 未重复提供账单不能降级已知费用，也不能阻塞合法产物/状态推进。
                known = True
                amount = row["actual_cost_minor"]
            else:
                fee = s.execute(
                    text(
                        "SELECT access.generation_fee_finish(:job,:state,:amount,:currency,:proof)"
                    ),
                    dict(
                        job=identifier,
                        state=outcome.state,
                        amount=outcome.amount_minor,
                        currency=outcome.currency,
                        proof=outcome.proof_hash,
                    ),
                ).scalar_one()
                known = fee == "SETTLED" and outcome.state == "KNOWN"
                amount = outcome.amount_minor if known else None
            s.execute(
                text("""UPDATE generation_jobs SET status=:status,submit_state=:submit,
                external_job_id=coalesce(external_job_id,:external),result_asset_id=:asset,
                billing_state=:billing,actual_cost_minor=:amount,version=version+1,
                updated_at=clock_timestamp() WHERE id=:job"""),
                dict(
                    job=identifier,
                    status=status,
                    submit=submit,
                    external=result.external_id,
                    asset=asset,
                    billing="KNOWN" if known else "UNKNOWN",
                    amount=amount,
                ),
            )
            if status == "FAILED" and not terminal:
                self._release(s, row, identifier)
            return self.repo.view(self.repo.job(s, identifier))

    def _release(self, s: Any, row: Any, identifier: UUID) -> None:
        for asset in sorted(row["assets_snapshot"]):
            self.repo.assets.reference_in_transaction(
                s, UUID(asset), "GENERATION", identifier, active=False
            )
        s.execute(
            text("""UPDATE workflow_run_steps SET status='FAILED'
            WHERE id=:step AND attempt=:attempt AND status='RUNNING'"""),
            dict(step=row["step_id"], attempt=row["step_attempt"]),
        )
        s.execute(
            text("UPDATE workflow_runs SET revision=revision+1 WHERE id=:run"),
            dict(run=row["run_id"]),
        )

    def cancel_unsent(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            row = self.repo.job(s, identifier, lock=True)
            self._lock_assets(s, row)
            if row["status"] == "CANCELLED":
                return self.repo.view(row)
            if row["submit_state"] != "NOT_SENT":
                raise ApiError(
                    ErrorCode.RESULT_UNKNOWN, "已可能发送，仅可按原键查询，不假称已取消/退款"
                )
            if not s.execute(
                text("SELECT access.generation_fee_cancel_unsent(:job)"), dict(job=identifier)
            ).scalar_one():
                raise ApiError(ErrorCode.LOCKED, "费用许可不能取消，保留账本")
            s.execute(
                text(
                    "UPDATE generation_jobs SET status='CANCELLED',version=version+1 WHERE id=:job"
                ),
                dict(job=identifier),
            )
            self._release(s, row, identifier)
            return self.repo.view(self.repo.job(s, identifier))
