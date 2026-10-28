"""真实系统策划模板brief→独立topic→采纳→planning；合成HTTP模型不付费。"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from yuliang.modules.aikernel.content_prompts import load_content_prompt
from yuliang.modules.content.ai_content_models import ContentPrepare
from yuliang.modules.content.models import Document
from yuliang.modules.content.workflows_execution_models import Execute
from yuliang.modules.content.workflows_execution_service import WorkflowExecution
from yuliang.modules.content.workflows_models import RunCreate
from yuliang.modules.content.workflows_service import WorkflowService
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_ai_content import ai_ready as ai_ready
from .test_ai_content import confirmation
from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import challenge, sign_in
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products
from .test_workflow_execution import accept_value, state

pytestmark = pytest.mark.integration
SYSTEM = UUID("00000000-0000-4000-8000-000000000064")
BRIEF = dict(
    audience="通勤人群",
    objective="介绍用途",
    sellingPoints="便携",
    channel="视频号",
    requirements="不夸大",
)


@pytest.fixture
def topic_ready(ai_ready: dict[str, Any]) -> dict[str, Any]:
    d = ai_ready
    prompt = load_content_prompt("content_topic")
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        s.execute(
            text(
                "INSERT INTO prompt_versions(tenant_id,prompt_key,version,capability_alia"
                "s,template,content_hash,output_schema_ref,status,created_by) "
                "VALUES(:tenant,'content_topic',1,'content.copywriting',:template,:hash,:"
                "schema,'ACTIVE',:actor)"
            ),
            dict(
                tenant=d["tenant"],
                template=prompt.template,
                hash=prompt.digest,
                schema=prompt.schema,
                actor=d["identity"].user_id,
            ),
        )
    return d


def system_line(
    d: dict[str, Any], *, brief: bool = True
) -> tuple[WorkflowExecution, UUID, ContentPrepare]:
    created = WorkflowService(d["content"]).create(
        RunCreate(templateId=str(SYSTEM), productId=d["product"], creationKey=uuid4().hex)
    )
    run = UUID(created["id"])
    execution = WorkflowExecution(d["content"])
    root = created["rootDocumentId"]
    if brief:
        pending = execution.execute(
            run,
            "brief",
            Execute(
                actionKey="brief-execute",
                expectedRunVersion=1,
                expectedAttempt=1,
                documentId=root,
                documentVersion=1,
                patch={"brief": BRIEF},
            ),
        )
        accepted = execution.accept(run, "brief", accept_value(pending, "brief", "brief-accept"))
        assert state(accepted, "topic")["status"] == "READY"
        assert state(accepted, "planning")["status"] == "PENDING"
    else:
        accepted = execution.get(run)
    document = d["content"].get(UUID(root))
    return (
        execution,
        run,
        ContentPrepare(
            requestKey="topic-request",
            expectedRunVersion=accepted["revision"],
            expectedAttempt=1,
            documentId=root,
            documentVersion=document["version"],
            aiScenario="content_topic",
        ),
    )


def test_system_brief_topic_model_candidate_accept_unlocks_planning_http(
    topic_ready: dict[str, Any],
) -> None:
    d = topic_ready
    execution, run, value = system_line(d)
    root = UUID(value.documentId)
    before = d["content"].get(root)
    assert before["brief"] == BRIEF and before["version"] == 2
    client = d["client"]
    client.cookies.clear()
    sign_in(d)
    headers = {"X-Shop-Id": str(d["shop"]), "X-CSRF-Token": challenge(client)}
    response = client.post(
        f"/v1/content/workflow-runs/{run}/steps/topic/ai/prepare",
        json=value.model_dump(),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    prepared = response.json()["data"]
    assert prepared["aiScenario"] == "content_topic" and d["calls"] == []
    produced = client.post(
        "/v1/content/ai-requests/" + prepared["id"] + "/confirm",
        json=confirmation(prepared).model_dump(),
        headers=headers,
    )
    assert produced.status_code == 200, produced.text
    assert produced.json()["data"]["state"] == "CANDIDATE" and d["content"].get(root) == before
    current = execution.get(run)
    assert current["revision"] == 5 and state(current, "topic")["status"] == "WAITING_REVIEW"
    assert state(current, "planning")["status"] == "PENDING"
    result = client.post(
        f"/v1/content/workflow-runs/{run}/steps/topic/accept",
        json=accept_value(current, "topic", "topic-accept").model_dump(),
        headers=headers,
    )
    assert result.status_code == 200, result.text
    accepted = result.json()["data"]
    assert accepted["revision"] == 6 and state(accepted, "topic")["status"] == "DONE"
    assert (
        state(accepted, "planning")["status"] == "READY"
        and state(accepted, "script")["status"] == "PENDING"
    )
    saved = d["content"].get(root)
    assert saved["body"] == "合成模型候选正文" and saved["brief"] == BRIEF and saved["version"] == 3
    assert len(d["calls"]) == 1 and "场景：content_topic" in d["calls"][0]["messages"][0]["content"]
    assert value.documentId not in d["calls"][0]["messages"][1]["content"]
    with business_transaction(d["engine"], d["identity"]) as s:
        proof = s.execute(
            text(
                "SELECT c.producer,p.prompt_key,p.version,p.content_hash,a.status FROM wo"
                "rkflow_step_candidates c "
                "JOIN ai_invocations a ON (a.tenant_id,a.call_id,a.prompt_id,a.model_config_id)="
                "(c.tenant_id,c.call_id,c.prompt_id,c.model_config_id) JOIN prompt_versio"
                "ns p ON (p.tenant_id,p.id)=(a.tenant_id,a.prompt_id) "
                "WHERE c.id=:id"
            ),
            dict(id=UUID(produced.json()["data"]["candidateId"])),
        ).one()
        assert tuple(proof) == (
            "MODEL",
            "content_topic",
            1,
            load_content_prompt("content_topic").digest,
            "OK",
        )


@pytest.mark.parametrize("failure", ["dependency", "wrong_step"])
def test_topic_requires_ready_dependency_and_its_declared_step(
    topic_ready: dict[str, Any], failure: str
) -> None:
    d = topic_ready
    _, run, value = system_line(d, brief=failure != "dependency")
    with pytest.raises(ApiError) as error:
        d["ai"].prepare(run, "planning" if failure == "wrong_step" else "topic", value)
    assert error.value.code in {ErrorCode.INVALID_INPUT, ErrorCode.ILLEGAL_TRANSITION}
    assert d["calls"] == []
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_ai_requests WHERE run_id=:run"), dict(run=run)
            ).scalar_one()
            == 0
        )


def test_old_topic_planning_alias_still_produces_from_original_prompt(
    topic_ready: dict[str, Any],
) -> None:
    d = topic_ready
    execution, run, value = system_line(d)
    prepared = d["ai"].prepare(
        run,
        "topic",
        value.model_copy(update={"aiScenario": "content_planning", "requestKey": "legacy-alias"}),
    )
    result = d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert result["state"] == "CANDIDATE" and result["aiScenario"] == "content_planning"
    assert "场景：content_planning" in d["calls"][0]["messages"][0]["content"]
    assert state(execution.get(run), "planning")["status"] == "PENDING"


def test_topic_late_reply_keeps_manual_document_and_does_not_unlock(
    topic_ready: dict[str, Any],
) -> None:
    d = topic_ready
    execution, run, value = system_line(d)
    root = UUID(value.documentId)
    prepared = d["ai"].prepare(run, "topic", value)

    def edit() -> None:
        d["content"].save(
            root, Document.model_validate(d["content"].get(root) | dict(body="人工已改选题"))
        )

    d["control"]["on_send"] = edit
    result = d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert result["state"] == "STALE" and d["content"].get(root)["body"] == "人工已改选题"
    assert state(execution.get(run), "planning")["status"] == "PENDING" and len(d["calls"]) == 1


def test_topic_extra_model_identity_is_rejected_without_formal_body_write(
    topic_ready: dict[str, Any],
) -> None:
    d = topic_ready
    execution, run, value = system_line(d)
    root = UUID(value.documentId)
    before = d["content"].get(root)
    prepared = d["ai"].prepare(run, "topic", value)
    d["control"]["failure"] = "identity"
    result = d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert (
        result["state"] == "FAILED"
        and result["candidateId"] is None
        and d["content"].get(root) == before
    )
    assert state(execution.get(run), "planning")["status"] == "PENDING"
