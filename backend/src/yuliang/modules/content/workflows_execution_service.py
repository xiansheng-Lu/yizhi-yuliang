"""人工候选/采纳与闭包重做；不伪造模型或供应商结果。"""

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.content.asset_models import AssetProof, document_assets
from yuliang.modules.content.assets import AssetService
from yuliang.modules.content.flows import FlowCreate, FlowService, derive_document
from yuliang.modules.content.models import Document
from yuliang.modules.content.service import ContentService, DocumentConflict
from yuliang.modules.content.validation import validate_content
from yuliang.modules.content.workflows_execution_models import Accept, Action, Execute, Redo, Skip
from yuliang.modules.content.workflows_execution_rules import downstream, satisfied
from yuliang.modules.content.workflows_models import TemplateDefinition
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class WorkflowExecution:
    def __init__(self, content: ContentService) -> None:
        self.content = content

    def _load(
        self, s: Session, identifier: UUID, write: bool = False
    ) -> tuple[Any, list[Any], TemplateDefinition]:
        query = (
            "SELECT * FROM workflow_runs WHERE id=:id FOR UPDATE"
            if write
            else "SELECT * FROM workflow_runs WHERE id=:id"
        )
        row = (
            s.execute(
                text(query),
                dict(id=identifier),
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "创作线不存在")
        self.content._shop(s, row["shop_id"], write)
        self.content._product(s, str(row["product_id"]), write)
        steps = list(
            s.execute(
                text("SELECT * FROM workflow_run_steps WHERE run_id=:id ORDER BY seq"),
                dict(id=identifier),
            ).mappings()
        )
        template = s.execute(
            text("SELECT definition FROM workflow_templates WHERE id=:id"),
            dict(id=row["template_id"]),
        ).scalar_one()
        return row, steps, TemplateDefinition.model_validate(template)

    def _view(self, s: Session, identifier: UUID) -> dict[str, Any]:
        run, steps, _ = self._load(s, identifier)
        size = s.execute(
            text(
                "SELECT coalesce(sum(octet_length(payload::text)),0) "
                "FROM workflow_step_candidates WHERE run_id=:id"
            ),
            dict(id=identifier),
        ).scalar_one()
        if size > 4 * 1024 * 1024:
            raise ApiError(ErrorCode.INVALID_INPUT, "候选历史超过4MiB读取上限，需分页接口")
        history = list(
            s.execute(
                text(
                    "SELECT action_key,step_code,action,reason,result->'revision' AS revision,"
                    "result->'steps' AS outputs FROM workflow_step_actions "
                    "WHERE run_id=:id ORDER BY (result->>'revision')::integer,action_key LIMIT 1001"
                ),
                dict(id=identifier),
            ).mappings()
        )
        if len(history) > 1000:
            raise ApiError(ErrorCode.INVALID_INPUT, "动作历史超过读取上限，需分页接口")
        candidates = list(
            s.execute(
                text(
                    "SELECT id,step_code,attempt,document_id,document_revision,payload,review_note "
                    "FROM workflow_step_candidates WHERE run_id=:id "
                    "ORDER BY created_at,id LIMIT 1001"
                ),
                dict(id=identifier),
            ).mappings()
        )
        if len(candidates) > 1000:
            raise ApiError(ErrorCode.INVALID_INPUT, "候选历史超过读取上限，需分页接口")
        return dict(
            id=str(identifier),
            revision=run["revision"],
            status=run["status"],
            rootDocumentId=str(run["root_document_id"]),
            history=[
                dict(
                    actionKey=x["action_key"],
                    code=x["step_code"],
                    action=x["action"],
                    reason=x["reason"],
                    revision=x["revision"],
                    outputs=x["outputs"],
                )
                for x in history
            ],
            steps=[
                dict(
                    code=x["step_code"],
                    status=x["status"],
                    attempt=x["attempt"],
                    candidateId=str(x["current_candidate_id"])
                    if x["current_candidate_id"]
                    else None,
                    documentId=str(x["document_id"]) if x["document_id"] else None,
                    skipReason=x["skip_reason"],
                )
                for x in steps
            ],
            candidates=[
                dict(
                    id=str(x["id"]),
                    code=x["step_code"],
                    attempt=x["attempt"],
                    documentId=str(x["document_id"]),
                    documentVersion=x["document_revision"],
                    document=x["payload"]["document"],
                    reviewNote=x["review_note"],
                )
                for x in candidates
            ],
        )

    def get(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            return self._view(s, identifier)

    def _begin(
        self, s: Session, identifier: UUID, code: str, action: str, value: Action
    ) -> tuple[Any, Any, TemplateDefinition, str, dict[str, Any] | None]:
        run, steps, template = self._load(s, identifier, True)
        digest = hashlib.sha256(
            json.dumps(
                dict(action=action, code=code, request=value.model_dump()), sort_keys=True
            ).encode()
        ).hexdigest()
        old = (
            s.execute(
                text("SELECT * FROM workflow_step_actions WHERE run_id=:run AND action_key=:key"),
                dict(run=identifier, key=value.actionKey),
            )
            .mappings()
            .first()
        )
        if old:
            if old["request_hash"] != digest:
                raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "动作键已用于其他请求")
            return run, None, template, digest, dict(old["result"])
        count = s.execute(
            text("SELECT count(*) FROM workflow_step_actions WHERE run_id=:id"), dict(id=identifier)
        ).scalar_one()
        if count >= 1000:
            raise ApiError(ErrorCode.INVALID_INPUT, "动作历史已达1000条；新动作拒绝，原键仍可重放")
        step = next((x for x in steps if x["step_code"] == code), None)
        if step is None:
            raise ApiError(ErrorCode.NO_DATA, "步骤不存在")
        if run["revision"] != value.expectedRunVersion or step["attempt"] != value.expectedAttempt:
            raise ApiError(ErrorCode.VERSION_CONFLICT, "创作线版本或步骤尝试已更新")
        if run["status"] == "ABANDONED" or run["status"] == "COMPLETED" and action != "redo":
            raise ApiError(ErrorCode.ILLEGAL_TRANSITION, "创作线已结束")
        return run, step, template, digest, None

    def _finish(
        self, s: Session, identifier: UUID, code: str, action: str, value: Action, digest: str
    ) -> dict[str, Any]:
        run, steps, template = self._load(s, identifier)
        validate_content(
            self.content._row(s, run["root_document_id"], True)["payload"], purpose="SOP"
        )
        statuses = {step["step_code"]: step["status"] for step in steps}
        for step in steps:
            if step["status"] == "PENDING" and satisfied(template, step["step_code"], statuses):
                s.execute(
                    text(
                        "UPDATE workflow_run_steps SET status='READY' "
                        "WHERE run_id=:run AND step_code=:code"
                    ),
                    dict(run=identifier, code=step["step_code"]),
                )
                statuses[step["step_code"]] = "READY"
        definitions = {step.code: step for step in template.steps}
        completed = all(
            state == "DONE"
            or (state == "SKIPPED" and template.schemaVersion == 2 and definitions[code].optional)
            for code, state in statuses.items()
        )
        s.execute(
            text("UPDATE workflow_runs SET revision=revision+1,status=:status WHERE id=:id"),
            dict(id=identifier, status="COMPLETED" if completed else "RUNNING"),
        )
        documents = (
            s.execute(
                text(
                    "SELECT document_id FROM content_document_references "
                    "WHERE reference_id=:id AND kind='RUN' ORDER BY document_id"
                ),
                dict(id=identifier),
            )
            .scalars()
            .all()
        )
        for document in documents:
            self.content.reference_in_transaction(s, document, identifier, "RUN", not completed)
        result = self._view(s, identifier)
        result["history"].append(
            dict(
                actionKey=value.actionKey,
                code=code,
                action=action,
                reason=getattr(value, "reason", None),
                revision=result["revision"],
                outputs=result["steps"],
            )
        )
        s.execute(
            text(
                "INSERT INTO workflow_step_actions(tenant_id,run_id,action_key,step_code,"
                "action,request_hash,result,reason,created_by) "
                "VALUES(:tenant,:run,:key,:code,:action,:hash,"
                "CAST(:result AS jsonb),:reason,:actor)"
            ),
            dict(
                tenant=self.content.identity.tenant_id,
                run=identifier,
                key=value.actionKey,
                code=code,
                action=action,
                hash=digest,
                result=json.dumps(result),
                reason=getattr(value, "reason", None),
                actor=self.content.identity.user_id,
            ),
        )
        return result

    def _assets(self, document: Document) -> dict[UUID, AssetProof] | None:
        if not document_assets(document):
            return None
        if self.content.shop is None:
            raise ApiError(ErrorCode.INVALID_INPUT, "需要明确店铺")
        return AssetService(
            self.content.engine, self.content.identity, self.content.shop, self.content.blob
        ).prepare_document(document)

    def execute(self, identifier: UUID, code: str, value: Execute) -> dict[str, Any]:
        value = Execute.model_validate(value.model_dump(warnings=False))
        # 实际固定版本文件预检在事务外；写事务重新检查输入版本和证明。
        with business_transaction(self.content.engine, self.content.identity) as s:
            run, steps, template = self._load(s, identifier, True)
            step = next((x for x in template.steps if x.code == code), None)
            if step is None:
                raise ApiError(ErrorCode.NO_DATA, "步骤不存在")
            if step.kind not in {"MANUAL", "REVIEW"}:
                raise ApiError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "内容模型候选/供应商生成尚未接入，未发起外部调用",
                )
            old = s.execute(
                text("SELECT 1 FROM workflow_step_actions WHERE run_id=:run AND action_key=:key"),
                dict(run=identifier, key=value.actionKey),
            ).first()
            if old:
                _, _, _, _, replay = self._begin(s, identifier, code, "execute", value)
                if replay is not None:
                    return replay
            current = self.content._row(s, UUID(value.documentId), True)
            if current["workflow_run_id"] != identifier:
                raise ApiError(ErrorCode.FORBIDDEN, "候选文档不属于此创作线")
            if current["revision"] != value.documentVersion:
                raise DocumentConflict(dict(current["payload"]))
            if (
                step.kind == "MANUAL"
                and not value.patch
                or step.kind == "REVIEW"
                and not value.reviewNote.strip()
            ):
                raise ApiError(
                    ErrorCode.INVALID_INPUT, "人工步骤需候选字段，审核步骤需明确审核说明"
                )
            candidate = validate_content(dict(current["payload"]) | value.patch, purpose="SOP")
            if value.deriveKind:
                candidate = validate_content(
                    derive_document(candidate, value.deriveKind), purpose="SOP"
                )
                if value.deriveKind != step.documentKind:
                    raise ApiError(ErrorCode.INVALID_INPUT, "派生类型与模板步骤不一致")
            elif candidate.kind != step.documentKind:
                raise ApiError(ErrorCode.INVALID_INPUT, "文档类型与模板步骤不一致，需明确派生")
        proofs = self._assets(candidate)
        with business_transaction(self.content.engine, self.content.identity) as s:
            run, state, template, digest, replay = self._begin(
                s, identifier, code, "execute", value
            )
            if replay is not None:
                return replay
            statuses = {x["step_code"]: x["status"] for x in self._load(s, identifier)[1]}
            if state["status"] != "READY" or not satisfied(template, code, statuses):
                raise ApiError(ErrorCode.ILLEGAL_TRANSITION, "步骤尚不可执行")
            self.content._lock(s, UUID(value.documentId))
            current = self.content._row(s, UUID(value.documentId), True)
            if current["revision"] != value.documentVersion:
                raise DocumentConflict(dict(current["payload"]))
            if current["workflow_run_id"] != identifier:
                raise ApiError(ErrorCode.FORBIDDEN, "候选文档不属于此创作线")
            AssetService(
                self.content.engine, self.content.identity, run["shop_id"], self.content.blob
            ).validate_document(s, candidate, proofs)
            candidate_id = uuid4()
            frozen = candidate.model_dump(exclude_none=True)
            frozen["parentId"] = candidate.parentId
            s.execute(
                text(
                    "INSERT INTO workflow_step_candidates(id,tenant_id,run_id,step_code,attempt,"
                    "document_id,document_revision,payload,derive_kind,review_note,created_by) "
                    "VALUES(:id,:tenant,:run,:code,:attempt,:doc,:version,"
                    "CAST(:payload AS jsonb),:kind,:note,:actor)"
                ),
                dict(
                    id=candidate_id,
                    tenant=self.content.identity.tenant_id,
                    run=identifier,
                    code=code,
                    attempt=state["attempt"],
                    doc=UUID(value.documentId),
                    version=value.documentVersion,
                    payload=json.dumps(dict(document=frozen, patch=value.patch)),
                    kind=value.deriveKind,
                    note=value.reviewNote,
                    actor=self.content.identity.user_id,
                ),
            )
            s.execute(
                text(
                    "UPDATE workflow_run_steps SET status='WAITING_REVIEW',"
                    "current_candidate_id=:candidate "
                    "WHERE run_id=:run AND step_code=:code"
                ),
                dict(candidate=candidate_id, run=identifier, code=code),
            )
            return self._finish(s, identifier, code, "execute", value, digest)

    def accept(self, identifier: UUID, code: str, value: Accept) -> dict[str, Any]:
        value = Accept.model_validate(value.model_dump(warnings=False))
        with business_transaction(self.content.engine, self.content.identity) as s:
            self._load(s, identifier, True)
            candidate = (
                s.execute(
                    text(
                        "SELECT * FROM workflow_step_candidates WHERE id=:id "
                        "AND run_id=:run AND step_code=:code"
                    ),
                    dict(id=UUID(value.candidateId), run=identifier, code=code),
                )
                .mappings()
                .first()
            )
            if candidate is None:
                raise ApiError(ErrorCode.NO_DATA, "候选不存在")
            old = s.execute(
                text("SELECT 1 FROM workflow_step_actions WHERE run_id=:run AND action_key=:key"),
                dict(run=identifier, key=value.actionKey),
            ).first()
            if old:
                _, _, _, _, replay = self._begin(s, identifier, code, "accept", value)
                if replay is not None:
                    return replay
            document = validate_content(candidate["payload"]["document"], purpose="SOP")
        proofs = self._assets(document) if not candidate["derive_kind"] else None
        with business_transaction(self.content.engine, self.content.identity) as s:
            run, step, _, digest, replay = self._begin(s, identifier, code, "accept", value)
            if replay is not None:
                return replay
            if (
                step["status"] != "WAITING_REVIEW"
                or step["current_candidate_id"] != UUID(value.candidateId)
                or candidate["attempt"] != step["attempt"]
            ):
                raise ApiError(ErrorCode.ILLEGAL_TRANSITION, "候选已失效或步骤不待采纳")
            if not candidate["derive_kind"]:
                self.content._lock(s, candidate["document_id"])
            current = self.content._row(s, candidate["document_id"], True)
            if (
                current["revision"] != value.documentVersion
                or candidate["document_revision"] != value.documentVersion
            ):
                raise DocumentConflict(dict(current["payload"]))
            if candidate["derive_kind"]:
                saved = FlowService(self.content).create_in_transaction(
                    s,
                    candidate["document_id"],
                    FlowCreate(
                        kind=candidate["derive_kind"],
                        version=value.documentVersion,
                        idempotency_key=f"run:{identifier}:step:{code}:attempt:{step['attempt']}:kind:{candidate['derive_kind']}",
                    ),
                    prepared=document,
                )
            else:
                saved = self.content._save(s, UUID(document.id), document, proofs)
            s.execute(
                text("UPDATE content_documents SET workflow_run_id=:run WHERE id=:doc"),
                dict(run=identifier, doc=UUID(saved["id"])),
            )
            self.content.reference_in_transaction(s, UUID(saved["id"]), identifier, "RUN")
            s.execute(
                text(
                    "UPDATE workflow_run_steps SET status='DONE',document_id=:doc "
                    "WHERE run_id=:run AND step_code=:code"
                ),
                dict(doc=UUID(saved["id"]), run=identifier, code=code),
            )
            return self._finish(s, identifier, code, "accept", value, digest)

    def skip(self, identifier: UUID, code: str, value: Skip) -> dict[str, Any]:
        value = Skip.model_validate(value.model_dump(warnings=False))
        with business_transaction(self.content.engine, self.content.identity) as s:
            _, step, template, digest, replay = self._begin(s, identifier, code, "skip", value)
            if replay is not None:
                return replay
            definition = next(x for x in template.steps if x.code == code)
            if step["status"] != "READY" or template.schemaVersion != 2 or not definition.optional:
                raise ApiError(
                    ErrorCode.ILLEGAL_TRANSITION, "仅新版模板明确可选且READY的步骤可跳过"
                )
            s.execute(
                text(
                    "UPDATE workflow_run_steps SET status='SKIPPED',skip_reason=:reason "
                    "WHERE run_id=:run AND step_code=:code"
                ),
                dict(reason=value.reason, run=identifier, code=code),
            )
            return self._finish(s, identifier, code, "skip", value, digest)

    def redo(self, identifier: UUID, code: str, value: Redo) -> dict[str, Any]:
        value = Redo.model_validate(value.model_dump(warnings=False))
        with business_transaction(self.content.engine, self.content.identity) as s:
            _, step, template, digest, replay = self._begin(s, identifier, code, "redo", value)
            if replay is not None:
                return replay
            if step["status"] not in {"DONE", "SKIPPED", "FAILED", "WAITING_REVIEW"}:
                raise ApiError(ErrorCode.ILLEGAL_TRANSITION, "步骤当前无可重做结果")
            closure = downstream(template, code)
            for state in self._load(s, identifier)[1]:
                if state["step_code"] in closure and state["status"] == "RUNNING":
                    raise ApiError(
                        ErrorCode.RESULT_UNKNOWN, "闭包存在在途外部动作，需先对账，不会重复发起"
                    )
            for affected in closure:
                s.execute(
                    text(
                        "UPDATE workflow_run_steps SET status='PENDING',attempt=attempt+1,"
                        "current_candidate_id=NULL,document_id=NULL,skip_reason=NULL "
                        "WHERE run_id=:run AND step_code=:code"
                    ),
                    dict(run=identifier, code=affected),
                )
            return self._finish(s, identifier, code, "redo", value, digest)
