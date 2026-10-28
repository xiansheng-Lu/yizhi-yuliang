"""真实人工SOP候选、采纳、重做、版本与事务；无模型/供应商调用。"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.content.service import ContentService
from yuliang.modules.content.workflows_execution_models import Accept, Execute, Redo, Skip
from yuliang.modules.content.workflows_execution_service import WorkflowExecution
from yuliang.modules.content.workflows_models import RunCreate, TemplateDefinition
from yuliang.modules.content.workflows_service import WorkflowService
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

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

pytestmark = pytest.mark.integration
SYSTEM = UUID("00000000-0000-4000-8000-000000000064")


def create(
    d: dict[str, Any], steps: list[dict[str, Any]], version: int = 2
) -> tuple[WorkflowExecution, UUID]:
    body: dict[str, Any] = dict(code="manual_test", name="人工工作流", steps=steps)
    if version == 2:
        body["schemaVersion"] = 2
    service = WorkflowService(d["content"])
    tpl = service.append_template(SYSTEM, TemplateDefinition.model_validate(body))
    run = service.create(
        RunCreate(templateId=tpl["id"], productId=d["product"], creationKey=uuid4().hex)
    )
    return WorkflowExecution(d["content"]), UUID(run["id"])


def definition(code: str, **kwargs: Any) -> dict[str, Any]:
    return dict(code=code, name=code, kind="MANUAL", documentKind="planning") | kwargs


def state(value: dict[str, Any], code: str) -> dict[str, Any]:
    return next(x for x in value["steps"] if x["code"] == code)


def execute_value(
    d: dict[str, Any],
    r: WorkflowExecution,
    run: UUID,
    code: str,
    key: str | None = None,
    **kwargs: Any,
) -> Execute:
    current = r.get(run)
    step = state(current, code)
    doc = d["content"].get(UUID(current["rootDocumentId"]))
    return Execute.model_validate(
        dict(
            actionKey=key or uuid4().hex,
            expectedRunVersion=current["revision"],
            expectedAttempt=step["attempt"],
            documentId=doc["id"],
            documentVersion=doc["version"],
            patch={"body": "人工候选" + code},
        )
        | kwargs
    )


def accept_value(current: dict[str, Any], code: str, key: str | None = None) -> Accept:
    step = state(current, code)
    candidate = next(x for x in current["candidates"] if x["id"] == step["candidateId"])
    return Accept(
        actionKey=key or uuid4().hex,
        expectedRunVersion=current["revision"],
        expectedAttempt=step["attempt"],
        candidateId=candidate["id"],
        documentVersion=candidate["documentVersion"],
    )


def complete(d: dict[str, Any], r: WorkflowExecution, run: UUID, code: str) -> dict[str, Any]:
    executed = r.execute(run, code, execute_value(d, r, run, code))
    return r.accept(run, code, accept_value(executed, code))


@pytest.mark.asyncio
async def test_concurrent_execute_accept_replay_revision_and_no_early_write(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r, run = create(d, [definition("brief")])
    initial = r.get(run)
    root = UUID(initial["rootDocumentId"])
    before = d["content"].get(root)
    value = execute_value(d, r, run, "brief", "execute")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: r.execute(run, "brief", value), range(2)))
    assert results[0] == results[1] and results[0]["revision"] == 2
    assert results[0] == r.get(run)
    assert results[0]["history"][-1]["actionKey"] == "execute"
    assert d["content"].get(root) == before
    candidate = accept_value(results[0], "brief", "accept")
    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(pool.map(lambda _: r.accept(run, "brief", candidate), range(2)))
    assert accepted[0] == accepted[1] and accepted[0]["revision"] == 3
    assert accepted[0] == r.get(run)
    assert accepted[0]["history"][-1]["actionKey"] == "accept"
    assert accepted[0]["status"] == "COMPLETED"
    assert d["content"].get(root)["body"] == "人工候选brief"
    assert d["content"].get(root)["version"] == 2
    assert r.execute(run, "brief", value) == results[0]
    with pytest.raises(ApiError) as conflict:
        r.execute(run, "brief", value.model_copy(update={"patch": {"body": "different"}}))
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_MISMATCH
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM workflow_step_candidates WHERE run_id=:run"),
                dict(run=run),
            ).scalar_one()
            == 1
        )
        assert (
            s.execute(
                text(
                    "SELECT count(*) FROM content_document_references "
                    "WHERE reference_id=:run AND active"
                ),
                dict(run=run),
            ).scalar_one()
            == 0
        )


@pytest.mark.asyncio
async def test_manual_edit_conflict_and_redo_preserve_history(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r, run = create(d, [definition("brief")])
    pending = r.execute(run, "brief", execute_value(d, r, run, "brief"))
    root = UUID(pending["rootDocumentId"])
    from yuliang.modules.content.models import Document

    edited = Document.model_validate(d["content"].get(root) | dict(body="人工编辑保留"))
    d["content"].save(root, edited)
    with pytest.raises(ApiError) as error:
        r.accept(run, "brief", accept_value(pending, "brief"))
    assert error.value.code == ErrorCode.VERSION_CONFLICT
    assert r.get(run)["revision"] == 2 and d["content"].get(root)["body"] == "人工编辑保留"
    redo = r.redo(
        run,
        "brief",
        Redo(actionKey="redo", expectedRunVersion=2, expectedAttempt=1, reason="输入改变"),
    )
    assert redo["revision"] == 3 and state(redo, "brief")["attempt"] == 2
    assert len(redo["candidates"]) == 1
    with pytest.raises(ApiError):
        r.accept(
            run,
            "brief",
            accept_value(pending, "brief").model_copy(
                update={"expectedRunVersion": 3, "expectedAttempt": 2}
            ),
        )


@pytest.mark.asyncio
async def test_optional_skip_required_denial_and_explicit_dependency(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r, run = create(
        d,
        [
            definition("required"),
            definition("optional", optional=True),
            definition("allowed", dependsOn=["optional"], optionalDependsOn=["optional"]),
            definition("blocked", dependsOn=["optional"]),
        ],
    )
    with pytest.raises(ApiError):
        r.skip(
            run,
            "required",
            Skip(actionKey="bad", expectedRunVersion=1, expectedAttempt=1, reason="不能跳"),
        )
    skipped = r.skip(
        run,
        "optional",
        Skip(actionKey="skip", expectedRunVersion=1, expectedAttempt=1, reason="无补充资料"),
    )
    assert skipped["revision"] == 2 and state(skipped, "allowed")["status"] == "READY"
    assert state(skipped, "blocked")["status"] == "PENDING"
    legacy, oldrun = create(d, [definition("legacy")], 1)
    with pytest.raises(ApiError):
        legacy.skip(
            oldrun,
            "legacy",
            Skip(actionKey="legacy", expectedRunVersion=1, expectedAttempt=1, reason="不允许"),
        )


@pytest.mark.asyncio
async def test_redo_dependency_closure_keeps_independent_confirmed_branch(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r, run = create(
        d,
        [
            definition("a"),
            definition("b", dependsOn=["a"]),
            definition("c", dependsOn=["a"]),
            definition("d", dependsOn=["b", "c"]),
            definition("independent"),
        ],
    )
    for code in ["independent", "a", "b", "c", "d"]:
        final = complete(d, r, run, code)
    assert final["status"] == "COMPLETED" and final["revision"] == 11
    revised = r.redo(
        run, "b", Redo(actionKey="redo-b", expectedRunVersion=11, expectedAttempt=1, reason="修改b")
    )
    assert revised["revision"] == 12 and revised["status"] == "RUNNING"
    assert state(revised, "b")["status"] == "READY" and state(revised, "d")["status"] == "PENDING"
    for code in ["a", "c", "independent"]:
        assert state(revised, code)["status"] == "DONE" and state(revised, code)["attempt"] == 1
    assert len(revised["candidates"]) == 5


@pytest.mark.asyncio
async def test_derivation_accept_and_failure_atomic_rollback(
    content_ready: dict[str, Any], monkeypatch: Any
) -> None:
    d = content_ready
    r, run = create(d, [definition("script", documentKind="scripts")])
    pending = r.execute(run, "script", execute_value(d, r, run, "script", deriveKind="scripts"))
    request = accept_value(pending, "script", "accept-derive")
    original = r._finish

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("after document and flow")

    monkeypatch.setattr(r, "_finish", fail)
    with pytest.raises(RuntimeError):
        r.accept(run, "script", request)
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM content_derivations")).scalar_one() == 0
        assert s.execute(text("SELECT count(*) FROM content_documents")).scalar_one() == 1
    assert r.get(run)["revision"] == 2
    monkeypatch.setattr(r, "_finish", original)
    accepted = r.accept(run, "script", request)
    child = UUID(state(accepted, "script")["documentId"])
    assert (
        d["content"].get(child)["kind"] == "scripts"
        and d["content"].get(child)["body"] == "人工候选script"
    )
    assert r.accept(run, "script", request) == accepted
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM content_derivations")).scalar_one() == 1
        assert s.execute(text("SELECT count(*) FROM content_documents")).scalar_one() == 2


@pytest.mark.asyncio
async def test_unavailable_ai_generation_no_fake_candidates_or_revision(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r, run = create(
        d, [definition("ai", kind="AI_ASSISTED"), definition("generate", kind="GENERATION")]
    )
    for code in ["ai", "generate"]:
        with pytest.raises(ApiError) as error:
            r.execute(run, code, execute_value(d, r, run, code))
        assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
    assert r.get(run)["revision"] == 1 and r.get(run)["candidates"] == []


@pytest.mark.asyncio
async def test_real_http_validation_csrf_and_scope(content_ready: dict[str, Any]) -> None:
    d = content_ready
    r, run = create(d, [definition("manual")])
    client = d["client"]
    client.cookies.clear()
    sign_in(d)
    headers = {"X-CSRF-Token": challenge(client), "X-Shop-Id": str(d["shop"])}
    path = f"/v1/content/workflow-runs/{run}/steps/manual/execute"
    value = execute_value(d, r, run, "manual").model_dump()
    assert client.post(path, json=value, headers={"X-Shop-Id": str(d["shop"])}).status_code == 403
    assert (
        client.post(
            path, json=value | dict(patch={"productId": str(uuid4())}), headers=headers
        ).status_code
        == 422
    )
    assert client.post(path, json=value, headers=headers).status_code == 200
    response = client.get(f"/v1/content/workflow-runs/{run}/execution", headers=headers)
    assert response.status_code == 200 and response.json()["data"]["revision"] == 2
    other = WorkflowExecution(ContentService(d["engine"], d["identity"], uuid4()))
    with pytest.raises(ApiError):
        other.get(run)


@pytest.mark.asyncio
async def test_review_requires_note_and_database_history_revision_guards(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r, run = create(d, [definition("review", kind="REVIEW")])
    with pytest.raises(ApiError):
        r.execute(run, "review", execute_value(d, r, run, "review", patch={}))
    pending = r.execute(
        run, "review", execute_value(d, r, run, "review", patch={}, reviewNote="已人工检查内容")
    )
    accepted = r.accept(run, "review", accept_value(pending, "review"))
    assert accepted["status"] == "COMPLETED"
    for query in [
        "UPDATE workflow_runs SET revision=revision WHERE id=:id",
        "DELETE FROM workflow_step_candidates WHERE run_id=:id",
        "UPDATE workflow_step_actions SET result='{}' WHERE run_id=:id",
    ]:
        with pytest.raises(DBAPIError), business_transaction(d["engine"], d["identity"]) as s:
            s.execute(text(query), dict(id=run))


@pytest.mark.asyncio
async def test_invalid_candidate_and_inflight_closure_do_not_advance(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r, run = create(d, [definition("a"), definition("downstream", dependsOn=["a"])])
    bad = execute_value(
        d,
        r,
        run,
        "a",
        patch={
            "scenes": [
                {"id": "invalid", "title": "x", "narration": "x", "visual": "x", "duration": 0.01}
            ]
        },
    )
    with pytest.raises(ApiError):
        r.execute(run, "a", bad)
    assert r.get(run)["revision"] == 1 and r.get(run)["candidates"] == []
    current = complete(d, r, run, "a")
    # 技术负例：维护角色构造已有在途状态；不伪造供应商job或发送事实。
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        s.execute(
            text(
                "UPDATE workflow_run_steps SET status='RUNNING' "
                "WHERE run_id=:id AND step_code='downstream'"
            ),
            dict(id=run),
        )
    with pytest.raises(ApiError) as error:
        r.redo(
            run,
            "a",
            Redo(
                actionKey="blocked-redo",
                expectedRunVersion=current["revision"],
                expectedAttempt=1,
                reason="需先对账",
            ),
        )
    assert error.value.code == ErrorCode.RESULT_UNKNOWN
    assert r.get(run)["revision"] == current["revision"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["graphics", "videos"])
async def test_derived_candidate_is_frozen_once_with_new_prompt_and_node_text(
    content_ready: dict[str, Any], kind: str
) -> None:
    from yuliang.modules.content.models import Document

    d = content_ready
    r, run = create(d, [definition("creative", documentKind=kind)])
    root = UUID(r.get(run)["rootDocumentId"])
    d["content"].save(root, Document.model_validate(d["content"].get(root) | dict(body="OLD")))
    pending = r.execute(
        run,
        "creative",
        execute_value(d, r, run, "creative", patch={"body": "NEW"}, deriveKind=kind),
    )
    frozen = pending["candidates"][0]["document"]
    assert frozen["id"] != str(root) and frozen["kind"] == kind and frozen["version"] == 1
    if kind == "graphics":
        assert frozen["prompt"] == "NEW"
    else:
        assert next(x for x in frozen["nodes"] if x["type"] == "generate")["text"] == "NEW"
    accepted = r.accept(run, "creative", accept_value(pending, "creative"))
    identifier = UUID(state(accepted, "creative")["documentId"])
    assert str(identifier) == frozen["id"]
    saved = d["content"].get(identifier)
    assert saved["version"] == 1
    assert {k: v for k, v in saved.items() if k != "updatedAt"} == {
        k: v for k, v in frozen.items() if k != "updatedAt"
    }
    assert d["content"].get(root)["body"] == "OLD"
    assert accepted == r.get(run)


@pytest.mark.asyncio
async def test_action_1001_rejected_before_any_state_document_or_event_write(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r, run = create(d, [definition("brief")])
    value = execute_value(d, r, run, "brief")
    root = UUID(r.get(run)["rootDocumentId"])
    before_document = d["content"].get(root)
    # 维护角色只构造配额边界，不声称1000次业务执行或付费调用。
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        s.execute(
            text(
                "INSERT INTO workflow_step_actions(tenant_id,run_id,action_key,step_code,action,"
                "request_hash,result,created_by) SELECT :tenant,:run,'quota-'||i,'brief','redo',"
                "repeat('a',64),jsonb_build_object('revision',i,'steps','[]'::jsonb),:actor "
                "FROM generate_series(1,1000) AS i"
            ),
            dict(tenant=d["identity"].tenant_id, run=run, actor=d["identity"].user_id),
        )
    before = r.get(run)
    with business_transaction(d["engine"], d["identity"]) as s:
        events = s.execute(
            text("SELECT count(*) FROM event_projections WHERE workflow_run_id=:run"), dict(run=run)
        ).scalar_one()
    with pytest.raises(ApiError) as error:
        r.execute(run, "brief", value)
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert r.get(run) == before and len(before["history"]) == 1000
    assert d["content"].get(root) == before_document
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM workflow_step_candidates WHERE run_id=:run"),
                dict(run=run),
            ).scalar_one()
            == 0
        )
        assert (
            s.execute(
                text("SELECT count(*) FROM workflow_step_actions WHERE run_id=:run"), dict(run=run)
            ).scalar_one()
            == 1000
        )
        assert (
            s.execute(
                text("SELECT count(*) FROM event_projections WHERE workflow_run_id=:run"),
                dict(run=run),
            ).scalar_one()
            == events
        )
