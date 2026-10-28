"""内容候选prepare/确认/一次发送；调用事实与SOP生产同事务落地。"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.aikernel.content_gateway import (
    ContentPort,
    ContentPrepared,
    ContentResult,
    prepare_content,
)
from yuliang.modules.aikernel.content_prompts import load_content_prompt
from yuliang.modules.aikernel.cost_gate import CostGate
from yuliang.modules.aikernel.cost_models import BillingOutcome
from yuliang.modules.aikernel.ledger import InvocationLedger
from yuliang.modules.aikernel.models import Completion, InvocationRequest, ServerSecrets
from yuliang.modules.content.ai_content_context import content_hash, context_from, safe_text
from yuliang.modules.content.ai_content_models import ContentConfirm, ContentPrepare
from yuliang.modules.content.asset_models import AssetProof, document_assets
from yuliang.modules.content.assets import AssetService
from yuliang.modules.content.service import ContentService
from yuliang.modules.content.workflow_model_production import ModelProduction
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


@dataclass(frozen=True)
class ContentRuntime:
    port: ContentPort
    secrets: ServerSecrets


class AIContent:
    def __init__(self, content: ContentService, runtime: ContentRuntime | None) -> None:
        self.content = content
        self.runtime = runtime
        self.production = ModelProduction(content)
        self.ledger = InvocationLedger(content.engine, content.identity)
        self.gate = CostGate(content.engine, content.identity)

    def _runtime(self) -> ContentRuntime:
        if self.runtime is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "内容模型未启用，未发起付费调用")
        return self.runtime

    def _row(self, s: Session, identifier: UUID, *, write: bool = False) -> Any:
        row = (
            s.execute(
                text(
                    "SELECT r.*,p.state AS permit_state FROM content_ai_requests r "
                    "LEFT JOIN ai_cost_permits p ON (p.tenant_id,p.id)=(r.tenant_id,r.permit_id) "
                    "WHERE r.id=:id"
                ),
                dict(id=identifier),
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "内容AI请求不存在")
        self.production.execution._load(s, row["run_id"], write)
        if write:
            if row["actor_id"] != self.content.identity.user_id:
                raise ApiError(ErrorCode.FORBIDDEN, "费用确认需原请求actor")
            row = (
                s.execute(
                    text(
                        "SELECT r.*,p.state AS permit_state FROM content_ai_requests r "
                        "LEFT JOIN ai_cost_permits p ON "
                        "(p.tenant_id,p.id)=(r.tenant_id,r.permit_id) "
                        "WHERE r.id=:id FOR UPDATE OF r"
                    ),
                    dict(id=identifier),
                )
                .mappings()
                .one()
            )
        return row

    @staticmethod
    def view(row: Any) -> dict[str, Any]:
        gateway = row["prepared"]["gateway"]
        quote = gateway["quote"]
        return dict(
            id=str(row["id"]),
            runId=str(row["run_id"]),
            code=row["step_code"],
            state=row["state"],
            reason=row["reason"]
            or ("IN_FLIGHT_NO_AUTOMATIC_RESEND" if row["state"] == "IN_FLIGHT" else None),
            inputVersion=row["input_revision"],
            attempt=row["attempt"],
            aiScenario=row["ai_scenario"],
            callId=str(row["call_id"]),
            promptId=str(row["prompt_id"]),
            modelConfigId=str(row["model_config_id"]),
            promptVersion=gateway["request"]["prompt_version"],
            model=gateway["route"]["selected"]["model"],
            contextHash=row["context_hash"],
            candidateId=str(row["candidate_id"]) if row["candidate_id"] else None,
            quote=dict(
                id=quote["quote_id"],
                hash=quote["quote_hash"],
                maximumCostMinor=quote["max_cost_minor"],
                currency=quote["currency"],
                expiresAt=quote["expires_at"],
            ),
            feeState=row.get("permit_state") or ("UNKNOWN" if row["permit_id"] else "NOT_STARTED"),
        )

    def get(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            return self.view(self._row(s, identifier))

    def _asset_service(self) -> AssetService:
        if self.content.shop is None:
            raise ApiError(ErrorCode.INVALID_INPUT, "需明确店铺")
        return AssetService(
            self.content.engine, self.content.identity, self.content.shop, self.content.blob
        )

    def _business_locks(self, s: Session, row: Any) -> None:
        """run/request已持有；文档→素材→费用，回包/撤权事实清理也遵守。

        只读当前请求绑定的内部引用ID，不读取或复制正文。先锁住所有run文档，
        再从不可变文档素材历史与本请求快照/在途引用求素材锁全集。
        """
        documents = set(
            s.execute(
                text(
                    "SELECT document_id FROM content_document_references "
                    "WHERE reference_id=:run AND kind='RUN'"
                ),
                dict(run=row["run_id"]),
            ).scalars()
        ) | {row["input_document_id"]}
        for identifier in sorted(documents, key=str):
            self.content._lock(s, identifier)
        assets = set(
            s.execute(
                text(
                    "SELECT asset_id FROM content_document_asset_versions "
                    "WHERE document_id=ANY(:docs) "
                    "UNION SELECT asset_id FROM content_asset_references "
                    "WHERE reference_id=:request AND kind='SOP'"
                ),
                dict(docs=list(documents), request=row["id"]),
            ).scalars()
        )
        assets.update(UUID(fact["id"]) for fact in row["asset_snapshots"])
        service = self._asset_service()
        for identifier in sorted(assets, key=str):
            service._lock(s, identifier)

    def _asset_facts(
        self, s: Session, identifiers: list[str], proofs: dict[UUID, AssetProof], product: UUID
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        from dataclasses import replace

        service = self._asset_service()
        context = []
        facts = []
        for raw in identifiers:
            identifier = UUID(raw)
            proof = proofs[identifier]
            row = service._row(s, identifier, write=True)
            if row["product_id"] is not None and row["product_id"] != product:
                raise ApiError(ErrorCode.FORBIDDEN, "素材不属于本商品")
            if replace(
                service._proof(row), checked_at=proof.checked_at, storage_token=proof.storage_token
            ) != proof or not service._attested(proof):
                raise ApiError(ErrorCode.LOCKED, "素材固定版本证明失效")
            context.append(dict(kind=row["kind"], name=row["name"]))
            facts.append(
                dict(
                    id=str(identifier),
                    version=proof.version,
                    sha256=proof.sha256,
                    mime=proof.mime,
                    size=proof.size,
                )
            )
        return context, facts

    def prepare(self, run: UUID, code: str, value: ContentPrepare) -> dict[str, Any]:
        try:
            return self._prepare(run, code, value)
        except DBAPIError as failure:
            state = getattr(failure.orig, "sqlstate", None)
            if state == "23505":
                raise ApiError(
                    ErrorCode.IDEMPOTENCY_MISMATCH, "内容请求键已用于其他请求，请使用新的稳定键"
                ) from None
            if state == "42501":
                raise ApiError(ErrorCode.FORBIDDEN, "当前身份不能创建内容调用") from None
            if state in {"22023", "P0001"}:
                raise ApiError(
                    ErrorCode.CAPABILITY_UNAVAILABLE, "内容路由、价格或预算不可用，未发送模型"
                ) from None
            raise
        except ValueError:
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "内容提示或模型配置不匹配，未发送模型"
            ) from None

    def _prepare(self, run: UUID, code: str, value: ContentPrepare) -> dict[str, Any]:
        self._runtime()
        value = ContentPrepare.model_validate(value.model_dump(warnings=False))
        digest = content_hash(dict(code=code, input=value.model_dump()))
        with business_transaction(self.content.engine, self.content.identity) as s:
            self.production.execution._load(s, run, True)
            old = (
                s.execute(
                    text(
                        "SELECT * FROM content_ai_requests WHERE run_id=:run AND request_key=:key"
                    ),
                    dict(run=run, key=value.requestKey),
                )
                .mappings()
                .first()
            )
            if old:
                if (
                    old["request_hash"] != digest
                    or old["actor_id"] != self.content.identity.user_id
                ):
                    raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "内容请求键已被使用")
                return self.view(old)
            self.production.inspect(s, run, code, value)
        proofs = {
            UUID(identifier): self._asset_service().prepare_reference(UUID(identifier))
            for identifier in value.assetIds
        }
        with business_transaction(self.content.engine, self.content.identity) as s:
            current, document = self.production.inspect(s, run, code, value)
            old = (
                s.execute(
                    text(
                        "SELECT * FROM content_ai_requests WHERE run_id=:run AND request_key=:key"
                    ),
                    dict(run=run, key=value.requestKey),
                )
                .mappings()
                .first()
            )
            if old:
                if (
                    old["request_hash"] != digest
                    or old["actor_id"] != self.content.identity.user_id
                ):
                    raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "内容请求键已被使用")
                return self.view(old)
            assets, facts = self._asset_facts(s, value.assetIds, proofs, current["product_id"])
            context = context_from(document, assets)
            prompt = load_content_prompt(value.aiScenario)
            identifier = uuid4()
            call = InvocationRequest(
                call_id=uuid4(),
                request_id=identifier,
                scenario="copywriting",
                capability_alias="content.copywriting",
                prompt_key=prompt.prompt_key,
                prompt_version=prompt.version,
            )
            prepared = prepare_content(self.ledger, self.gate, prompt, call, context, session=s)
            invocation = (
                s.execute(
                    text(
                        "SELECT prompt_id,model_config_id FROM ai_invocations WHERE call_id=:call"
                    ),
                    dict(call=call.call_id),
                )
                .mappings()
                .one()
            )
            s.execute(
                text(
                    "INSERT INTO content_ai_requests(id,tenant_id,shop_id,product_id,run_"
                    "id,step_code,attempt,actor_id,request_key,request_hash,"
                    "input_document_id,input_revision,ai_scenario,context_hash,context,as"
                    "set_snapshots,prepared,call_id,prompt_id,model_config_id,quote_id,st"
                    "ate) "
                    "VALUES(:id,:tenant,:shop,:product,:run,:code,:attempt,:actor,:key,:h"
                    "ash,:doc,:revision,:scenario,:context_hash,"
                    "CAST(:context AS jsonb),CAST(:assets AS jsonb),CAST(:prepared AS jso"
                    "nb),:call,:prompt,:config,:quote,'PREPARED')"
                ),
                dict(
                    id=identifier,
                    tenant=self.content.identity.tenant_id,
                    shop=current["shop_id"],
                    product=current["product_id"],
                    run=run,
                    code=code,
                    attempt=value.expectedAttempt,
                    actor=self.content.identity.user_id,
                    key=value.requestKey,
                    hash=digest,
                    doc=UUID(value.documentId),
                    revision=value.documentVersion,
                    scenario=value.aiScenario,
                    context_hash=content_hash(context.model_dump()),
                    context=context.model_dump_json(),
                    assets=json.dumps(facts),
                    prepared=json.dumps(
                        dict(input=value.model_dump(), gateway=prepared.model_dump(mode="json"))
                    ),
                    call=call.call_id,
                    prompt=invocation["prompt_id"],
                    config=invocation["model_config_id"],
                    quote=prepared.quote.quote_id,
                ),
            )
            return self.view(self._row(s, identifier))

    def confirm(self, identifier: UUID, value: ContentConfirm) -> dict[str, Any]:
        runtime = self._runtime()
        value = ContentConfirm.model_validate(value.model_dump(warnings=False))
        with business_transaction(self.content.engine, self.content.identity) as s:
            row = self._row(s, identifier, write=True)
            if row["state"] != "PREPARED":
                return self.view(row)
            prepared = ContentPrepared.model_validate_json(json.dumps(row["prepared"]["gateway"]))
            proposal = ContentPrepare.model_validate(row["prepared"]["input"])
            self.production.inspect(s, row["run_id"], row["step_code"], proposal)
        proofs = {
            UUID(asset["id"]): self._asset_service().prepare_reference(UUID(asset["id"]))
            for asset in row["asset_snapshots"]
        }
        key = runtime.secrets.resolve(prepared.route.selected)
        with business_transaction(self.content.engine, self.content.identity) as s:
            row = self._row(s, identifier, write=True)
            if row["state"] != "PREPARED":
                return self.view(row)
            self._business_locks(s, row)
            self.production.inspect(s, row["run_id"], row["step_code"], proposal)
            _, facts = self._asset_facts(s, proposal.assetIds, proofs, row["product_id"])
            if (
                facts != row["asset_snapshots"]
                or content_hash(row["context"]) != row["context_hash"]
            ):
                raise ApiError(ErrorCode.VERSION_CONFLICT, "内容上下文或素材版本已变化")
            safe_text(row["context"])
            load_content_prompt(proposal.aiScenario).bind(prepared.route.model_dump(mode="json"))
            if hashlib.sha256(prepared.body.encode()).hexdigest() != prepared.billable.payload_hash:
                raise ApiError(ErrorCode.FORBIDDEN, "内容调用载荷摘要不一致")
            if (value.quoteHash, value.maximumCostMinor, value.currency) != (
                prepared.quote.quote_hash,
                prepared.quote.max_cost_minor,
                prepared.quote.currency,
            ):
                raise ApiError(ErrorCode.VERSION_CONFLICT, "费用确认与实际报价不一致")
            self.gate.confirm_in_transaction(s, prepared.quote)
            permit = self.gate.acquire_in_transaction(s, prepared.quote)
            s.execute(
                text(
                    "UPDATE content_ai_requests SET state='IN_FLIGHT',permit_id=:permit,u"
                    "pdated_at=clock_timestamp() WHERE id=:id"
                ),
                dict(id=identifier, permit=permit.permit_id),
            )
            begun = self.production.begin(s, row)
            self.gate.start_in_transaction(s, permit, prepared.billable)
            s.execute(
                text("UPDATE content_ai_requests SET begin_revision=:revision WHERE id=:id"),
                dict(id=identifier, revision=begun["revision"]),
            )
            for asset, proof in proofs.items():
                self._asset_service().reference_in_transaction(
                    s, asset, "SOP", identifier, proof=proof
                )
        try:
            result = runtime.port.send(prepared, key)
        except Exception:
            result = ContentResult(
                Completion(status="FAILED", dispatch_state="UNKNOWN", latency_ms=0),
                reason="RESULT_UNKNOWN",
            )
        reply_error = None
        document_proofs = None
        if result.patch is not None:
            try:
                with business_transaction(self.content.engine, self.content.identity) as s:
                    current = self._row(s, identifier, write=True)
                    candidate, _ = self.production.candidate_document(s, current, result.patch)
                if document_assets(candidate):
                    document_proofs = self._asset_service().prepare_document(candidate)
            except ApiError as failure:
                reply_error = (
                    "SCOPE_CHANGED" if failure.code == ErrorCode.FORBIDDEN else "STALE_REPLY"
                )
        # 网络已发，任何落账失败都不能回到PREPARED或自动重发。
        try:
            with business_transaction(self.content.engine, self.content.identity) as s:
                row = (
                    s.execute(
                        text("SELECT * FROM content_ai_requests WHERE id=:id"), dict(id=identifier)
                    )
                    .mappings()
                    .one()
                )
                s.execute(
                    text("SELECT id FROM workflow_runs WHERE id=:id FOR UPDATE"),
                    dict(id=row["run_id"]),
                )
                row = (
                    s.execute(
                        text("SELECT * FROM content_ai_requests WHERE id=:id FOR UPDATE"),
                        dict(id=identifier),
                    )
                    .mappings()
                    .one()
                )
                self._business_locks(s, row)
                fee = s.execute(
                    text("SELECT state FROM ai_cost_permits WHERE id=:id"),
                    dict(id=permit.permit_id),
                ).scalar_one()
                if fee == "IN_FLIGHT":
                    self.gate.finish_in_transaction(s, permit, BillingOutcome())
                call_state = s.execute(
                    text("SELECT status FROM ai_invocations WHERE call_id=:id"),
                    dict(id=prepared.request.call_id),
                ).scalar_one()
                if call_state == "STARTED":
                    self.ledger.finish_in_transaction(s, prepared.request, result.completion)
                if row["state"] != "IN_FLIGHT":
                    return dict(id=str(identifier), state=row["state"], reason=row["reason"])
                if result.patch is None or reply_error is not None:
                    s.execute(
                        text("SELECT access.content_ai_failure(:id,:reason)"),
                        dict(id=identifier, reason=reply_error or result.reason),
                    )
                else:
                    try:
                        with s.begin_nested():
                            self.production.execution._load(s, row["run_id"], True)
                            s.execute(
                                text(
                                    "UPDATE content_ai_requests SET state='CANDIDATE' WHERE id=:id"
                                ),
                                dict(id=identifier),
                            )
                            self.production.finish(s, row, result.patch, document_proofs)
                    except ApiError as failure:
                        reason = (
                            "SCOPE_CHANGED"
                            if failure.code == ErrorCode.FORBIDDEN
                            else "STALE_REPLY"
                        )
                        s.execute(
                            text("SELECT access.content_ai_failure(:id,:reason)"),
                            dict(id=identifier, reason=reason),
                        )
                s.execute(
                    text(
                        "UPDATE content_asset_references SET active=false WHERE reference"
                        "_id=:id AND kind='SOP'"
                    ),
                    dict(id=identifier),
                )
                final = (
                    s.execute(
                        text("SELECT * FROM content_ai_requests WHERE id=:id"), dict(id=identifier)
                    )
                    .mappings()
                    .one()
                )
                return self.view(final)
        except Exception:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "内容调用已开始，结果/费用待核对；不会自动重发"
            ) from None
