"""生成账本范围和固定输入核验；不包含供应商调用。"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.aikernel.cost_models import BillableRequest, CostQuote
from yuliang.modules.content.asset_models import AssetProof
from yuliang.modules.content.assets import AssetService
from yuliang.modules.content.generation_models import GenerationInput
from yuliang.modules.content.models import Document
from yuliang.modules.content.service import ContentService
from yuliang.modules.content.workflows_execution_rules import satisfied
from yuliang.modules.content.workflows_execution_service import WorkflowExecution
from yuliang.platform.errors import ApiError, ErrorCode


class GenerationRepository:
    def __init__(self, content: ContentService) -> None:
        if content.shop is None:
            raise ValueError("生成需要明确当前店铺")
        self.content = content
        self.assets = AssetService(content.engine, content.identity, content.shop, content.blob)

    def input(
        self, s: Session, value: GenerationInput, *, lock: bool = False
    ) -> tuple[Any, Any, Document]:
        run, steps, template = WorkflowExecution(self.content)._load(s, UUID(value.runId), lock)
        step = next((v for v in steps if v["step_code"] == value.stepCode), None)
        definition = next((v for v in template.steps if v.code == value.stepCode), None)
        if (
            step is None
            or definition is None
            or definition.kind != "GENERATION"
            or run["revision"] != value.expectedRunVersion
            or step["attempt"] != value.expectedAttempt
            or step["status"] != "READY"
        ):
            raise ApiError(ErrorCode.VERSION_CONFLICT, "创作线版本/步骤/attempt或生成READY条件不符")
        statuses = {v["step_code"]: v["status"] for v in steps}
        if not satisfied(template, value.stepCode, statuses):
            raise ApiError(ErrorCode.ILLEGAL_TRANSITION, "生成步骤依赖未满足")
        row = self.content._row(s, UUID(value.documentId), True)
        if (
            row["workflow_run_id"] != run["id"]
            or row["revision"] != value.documentVersion
            or row["product_id"] != run["product_id"]
        ):
            raise ApiError(ErrorCode.VERSION_CONFLICT, "生成文档版本或商品/创作线范围不符")
        return run, step, Document.model_validate(row["payload"])

    def request(
        self, s: Session, value: GenerationInput, proofs: dict[UUID, AssetProof]
    ) -> tuple[BillableRequest, dict[str, Any]]:
        if value.kind not in {"IMAGE", "GRAPHIC", "VIDEO"}:
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "当前未确认此生成类型计费/媒体规格，禁止猜单位"
            )
        source = (
            s.execute(
                text("""SELECT p.* FROM generation_price_sources source
            JOIN ai_price_versions p ON (p.tenant_id,p.id)=(source.tenant_id,source.price_id)
            WHERE source.provider=:provider AND source.kind=:kind AND p.price_key=:key
            AND source.checked_at<=clock_timestamp()
            AND source.expires_at>clock_timestamp() AND p.kind='GENERATION'
            AND p.unit_scale=1 AND p.verification='VERIFIED' AND p.valid_from<=clock_timestamp()
            AND p.valid_until>clock_timestamp()
            AND ((source.unit_semantic='ONE_REQUEST_JOB' AND p.unit='JOB')
             OR (source.unit_semantic='ONE_REQUEST_SECOND' AND p.unit='SECOND'
              AND :kind='VIDEO'))
            ORDER BY p.revision DESC LIMIT 1"""),
                dict(provider=value.provider, kind=value.kind, key=value.priceKey),
            )
            .mappings()
            .first()
        )
        if source is None:
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "缺可信供应商价格/币种/单位来源，不估0费用"
            )
        if source["unit"] == "SECOND":
            # 按秒计价必须有明确的预期时长；缺失时显式拒绝而不是按0秒估费。
            if value.durationSeconds is None:
                raise ApiError(
                    ErrorCode.CAPABILITY_UNAVAILABLE, "按秒计价缺明确视频时长，禁止猜单位"
                )
            units = value.durationSeconds
        else:
            units = 1
        assets = {
            str(k): dict(version=v.version, sha256=v.sha256, kind=v.kind) for k, v in proofs.items()
        }
        snapshot = dict(input=value.model_dump(), assets=assets)
        payload = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        request = BillableRequest(
            request_key=UUID(value.requestKey),
            kind="GENERATION",
            scenario="content_generation",
            price_key=value.priceKey,
            input_units=units,
            output_units=0,
            payload_hash=payload,
        )
        return request, snapshot

    def job(self, s: Session, identifier: UUID, *, lock: bool = False) -> Any:
        pointer = s.execute(
            text("SELECT run_id FROM generation_jobs WHERE id=:id"), dict(id=identifier)
        ).first()
        if pointer is None:
            raise ApiError(ErrorCode.NO_DATA, "生成任务不存在")
        if lock:
            WorkflowExecution(self.content)._load(s, pointer.run_id, True)
        row = (
            s.execute(
                text(
                    "SELECT * FROM generation_jobs WHERE id=:id FOR UPDATE"
                    if lock
                    else "SELECT * FROM generation_jobs WHERE id=:id"
                ),
                dict(id=identifier),
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "生成任务不存在")
        self.content._row(s, row["document_id"], True)
        self.content._shop(s, row["shop_id"], True)
        self.content._product(s, str(row["product_id"]), True)
        return row

    def quote(
        self,
        s: Session,
        identifier: UUID,
        request: BillableRequest,
        provider: str,
        kind: str,
        *,
        confirmed: bool = False,
    ) -> CostQuote:
        row = (
            s.execute(
                text("""SELECT * FROM ai_cost_quotes WHERE id=:id AND actor_id=:actor
            AND request_key=:key AND kind='GENERATION' AND request_fingerprint=:fingerprint"""),
                dict(
                    id=identifier,
                    actor=self.content.identity.user_id,
                    key=request.request_key,
                    fingerprint=request.fingerprint(),
                ),
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.FORBIDDEN, "费用报价不属于当前生成输入/actor")
        trusted = s.execute(
            text("""SELECT 1 FROM generation_price_sources source
            JOIN ai_price_versions p ON (p.tenant_id,p.id)=(source.tenant_id,source.price_id)
            WHERE source.price_id=:price AND source.provider=:provider AND source.kind=:kind
            AND source.checked_at<=clock_timestamp()
            AND source.expires_at>clock_timestamp() AND p.kind='GENERATION'
            AND p.unit_scale=1 AND p.verification='VERIFIED' AND p.valid_from<=clock_timestamp()
            AND p.valid_until>clock_timestamp()
            AND ((source.unit_semantic='ONE_REQUEST_JOB' AND p.unit='JOB')
             OR (source.unit_semantic='ONE_REQUEST_SECOND' AND p.unit='SECOND'
              AND :kind='VIDEO'))"""),
            dict(price=row["price_id"], provider=provider, kind=kind),
        ).first()
        if trusted is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "实际报价价格版本缺供应商/单位来源")
        if row["expires_at"] <= datetime.now(UTC) or confirmed and row["confirmed_at"] is None:
            raise ApiError(ErrorCode.VERSION_CONFLICT, "生成报价已过期或尚未明确确认")
        return CostQuote(
            quote_id=row["id"],
            request_key=row["request_key"],
            max_cost_minor=row["max_cost_minor"],
            currency=row["currency"],
            expires_at=row["expires_at"],
            quote_hash=row["quote_hash"],
        )

    @staticmethod
    def view(row: Any) -> dict[str, Any]:
        return dict(
            id=str(row["id"]),
            requestKey=str(row["request_key"]),
            status=row["status"],
            submitState=row["submit_state"],
            version=row["version"],
            externalJobId=row["external_job_id"],
            estimatedCostMinor=row["estimated_cost_minor"],
            actualCostMinor=row["actual_cost_minor"],
            billingState=row["billing_state"],
            currency=row["currency"],
            errorCode=row["error_code"],
            resultAssetId=str(row["result_asset_id"]) if row["result_asset_id"] else None,
        )
