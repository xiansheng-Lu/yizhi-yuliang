"""SOP真实MODEL生产者；组合已有事务守卫，不伪装人工候选。"""

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.aikernel.content_prompts import load_content_prompt
from yuliang.modules.content.ai_content_models import ContentPrepare
from yuliang.modules.content.asset_models import AssetProof
from yuliang.modules.content.assets import AssetService
from yuliang.modules.content.flows import derive_document
from yuliang.modules.content.models import Document
from yuliang.modules.content.service import ContentService, DocumentConflict
from yuliang.modules.content.validation import validate_content
from yuliang.modules.content.workflows_execution_models import Action
from yuliang.modules.content.workflows_execution_service import WorkflowExecution
from yuliang.platform.errors import ApiError, ErrorCode

SCENARIO_STEPS = {
    "content_topic": {"topic"},
    "content_planning": {"planning", "topic"},
    "content_script": {"script"},
    "content_storyboard": {"storyboard"},
}


class ModelProduction:
    def __init__(self, content: ContentService) -> None:
        self.content = content
        self.execution = WorkflowExecution(content)

    def inspect(
        self, s: Session, run: UUID, code: str, value: ContentPrepare
    ) -> tuple[Any, Document]:
        current, steps, template = self.execution._load(s, run, True)
        step = next((x for x in steps if x["step_code"] == code), None)
        definition = next((x for x in template.steps if x.code == code), None)
        prompt = load_content_prompt(value.aiScenario)
        if step is None or definition is None:
            raise ApiError(ErrorCode.NO_DATA, "步骤不存在")
        if (
            definition.kind != "AI_ASSISTED"
            or code not in SCENARIO_STEPS[value.aiScenario]
            or definition.documentKind != prompt.kind
        ):
            raise ApiError(ErrorCode.INVALID_INPUT, "内容场景与冻结模板步骤不一致")
        if (
            current["status"] != "RUNNING"
            or current["revision"] != value.expectedRunVersion
            or step["attempt"] != value.expectedAttempt
        ):
            raise ApiError(ErrorCode.VERSION_CONFLICT, "创作线版本或尝试已更新")
        if step["status"] != "READY":
            raise ApiError(ErrorCode.ILLEGAL_TRANSITION, "步骤不在READY")
        document = self.content._row(s, UUID(value.documentId), True)
        if document["workflow_run_id"] != run:
            raise ApiError(ErrorCode.FORBIDDEN, "输入文档不属于此创作线")
        if document["revision"] != value.documentVersion:
            raise DocumentConflict(dict(document["payload"]))
        return current, validate_content(document["payload"], purpose="SOP")

    def begin(self, s: Session, row: Any) -> dict[str, Any]:
        value = ContentPrepare.model_validate(row["prepared"]["input"])
        self.inspect(s, row["run_id"], row["step_code"], value)
        action = Action(
            actionKey=f"model:{row['id']}:begin",
            expectedRunVersion=value.expectedRunVersion,
            expectedAttempt=value.expectedAttempt,
        )
        _, _, _, digest, replay = self.execution._begin(
            s, row["run_id"], row["step_code"], "model_begin", action
        )
        if replay is not None:
            return replay
        s.execute(
            text(
                "UPDATE workflow_run_steps SET status='RUNNING' WHERE run_id=:run AND ste"
                "p_code=:code"
            ),
            dict(run=row["run_id"], code=row["step_code"]),
        )
        return self.execution._finish(
            s, row["run_id"], row["step_code"], "model_begin", action, digest
        )

    def candidate_document(
        self, s: Session, row: Any, patch: dict[str, Any]
    ) -> tuple[Document, str | None]:
        current = self.content._row(s, row["input_document_id"], True)
        if current["revision"] != row["input_revision"]:
            raise DocumentConflict(dict(current["payload"]))
        prompt = load_content_prompt(row["ai_scenario"])
        if set(patch) != {prompt.field}:
            raise ApiError(ErrorCode.INVALID_INPUT, "模型候选不符合声明落点")
        candidate = validate_content(dict(current["payload"]) | patch, purpose="SOP")
        kind = prompt.kind
        if candidate.kind != kind:
            return validate_content(derive_document(candidate, kind), purpose="SOP"), kind
        return candidate, None

    def finish(
        self,
        s: Session,
        row: Any,
        patch: dict[str, Any],
        proofs: dict[UUID, AssetProof] | None = None,
    ) -> dict[str, Any]:
        run, steps, _ = self.execution._load(s, row["run_id"], True)
        step = next(x for x in steps if x["step_code"] == row["step_code"])
        if (
            run["revision"] != row["begin_revision"]
            or step["attempt"] != row["attempt"]
            or step["status"] != "RUNNING"
        ):
            raise ApiError(ErrorCode.VERSION_CONFLICT, "旧模型回复不再匹配当前创作线")
        action = Action(
            actionKey=f"model:{row['id']}:complete",
            expectedRunVersion=run["revision"],
            expectedAttempt=step["attempt"],
        )
        _, _, _, digest, replay = self.execution._begin(
            s, row["run_id"], row["step_code"], "model_complete", action
        )
        if replay is not None:
            return replay
        observed = s.execute(
            text(
                "SELECT 1 FROM ai_invocations WHERE call_id=:call AND prompt_id=:prompt "
                "AND model_config_id=:config AND scenario='copywriting' AND status='OK' "
                "AND dispatch_state='RESPONDED' AND user_id=:actor"
            ),
            dict(
                call=row["call_id"],
                prompt=row["prompt_id"],
                config=row["model_config_id"],
                actor=row["actor_id"],
            ),
        ).first()
        if observed is None:
            raise ApiError(ErrorCode.FORBIDDEN, "缺少实际内容调用完成事实，不能登记MODEL候选")
        self.content._lock(s, row["input_document_id"])
        candidate, kind = self.candidate_document(s, row, patch)
        AssetService(
            self.content.engine, self.content.identity, run["shop_id"], self.content.blob
        ).validate_document(s, candidate, proofs)
        frozen = candidate.model_dump(exclude_none=True)
        frozen["parentId"] = candidate.parentId
        identifier = uuid4()
        s.execute(
            text(
                "INSERT INTO workflow_step_candidates(id,tenant_id,run_id,step_code,attem"
                "pt,document_id,document_revision,"
                "payload,derive_kind,review_note,created_by,producer,call_id,prompt_id,mo"
                "del_config_id) "
                "VALUES(:id,:tenant,:run,:code,:attempt,:doc,:version,CAST(:payload AS js"
                "onb),:kind,'MODEL',:actor,"
                "'MODEL',:call,:prompt,:config)"
            ),
            dict(
                id=identifier,
                tenant=self.content.identity.tenant_id,
                run=row["run_id"],
                code=row["step_code"],
                attempt=row["attempt"],
                doc=row["input_document_id"],
                version=row["input_revision"],
                payload=json.dumps(dict(document=frozen, patch=patch)),
                kind=kind,
                actor=row["actor_id"],
                call=row["call_id"],
                prompt=row["prompt_id"],
                config=row["model_config_id"],
            ),
        )
        s.execute(
            text(
                "UPDATE workflow_run_steps SET status='WAITING_REVIEW',current_candidate_"
                "id=:candidate "
                "WHERE run_id=:run AND step_code=:code"
            ),
            dict(candidate=identifier, run=row["run_id"], code=row["step_code"]),
        )
        s.execute(
            text(
                "UPDATE content_ai_requests SET state='CANDIDATE',candidate_id=:candidate"
                ",reason='MODEL_CANDIDATE',updated_at=clock_timestamp() WHERE id=:id"
            ),
            dict(candidate=identifier, id=row["id"]),
        )
        return self.execution._finish(
            s, row["run_id"], row["step_code"], "model_complete", action, digest
        )

    def fail(
        self, s: Session, row: Any, reason: str, *, stale: bool = False
    ) -> dict[str, Any] | None:
        run, steps, _ = self.execution._load(s, row["run_id"], True)
        step = next(x for x in steps if x["step_code"] == row["step_code"])
        s.execute(
            text(
                "UPDATE content_ai_requests SET state=:state,reason=:reason,updated_at=cl"
                "ock_timestamp() WHERE id=:id"
            ),
            dict(id=row["id"], state="STALE" if stale else "FAILED", reason=reason),
        )
        if step["attempt"] != row["attempt"] or step["status"] != "RUNNING":
            return None
        action = Action(
            actionKey=f"model:{row['id']}:failure",
            expectedRunVersion=run["revision"],
            expectedAttempt=step["attempt"],
        )
        _, _, _, digest, replay = self.execution._begin(
            s, row["run_id"], row["step_code"], "model_failure", action
        )
        if replay is not None:
            return replay
        s.execute(
            text(
                "UPDATE workflow_run_steps SET status='FAILED' WHERE run_id=:run AND step"
                "_code=:code"
            ),
            dict(run=row["run_id"], code=row["step_code"]),
        )
        return self.execution._finish(
            s, row["run_id"], row["step_code"], "model_failure", action, digest
        )
