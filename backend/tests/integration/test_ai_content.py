"""真实PG/HTTP合成内容模型与费用/候选链；不访问真实供应商。"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.aikernel.content_gateway import ContentPort
from yuliang.modules.aikernel.content_prompts import load_content_prompt
from yuliang.modules.aikernel.models import ModelSpec, ServerSecrets
from yuliang.modules.content.ai_content_models import ContentConfirm, ContentPrepare, Scenario
from yuliang.modules.content.ai_content_service import AIContent, ContentRuntime
from yuliang.modules.content.models import Document
from yuliang.modules.content.workflows_execution_service import WorkflowExecution
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_configuration_context import configuration_ready as configuration_ready
from .test_content_assets import assets_ready as assets_ready
from .test_content_uploads import blob as blob
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import challenge, sign_in
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products
from .test_workflow_execution import accept_value, create, definition

pytestmark = pytest.mark.integration


@pytest.fixture
async def ai_ready(content_ready: dict[str, Any]) -> dict[str, Any]:
    d = content_ready
    calls = []
    control: dict[str, Any] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if control.get("on_send"):
            control["on_send"]()
        if control.get("failure") == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if control.get("failure") in {429, 503}:
            return httpx.Response(control["failure"])
        output: dict[str, Any] = {"body": "合成模型候选正文"}
        if "content_storyboard" in body["messages"][0]["content"]:
            output = {
                "scenes": [
                    dict(title="首镜", narration="合成旁白", visual="商品画面", duration=3.0)
                ]
            }
        if control.get("failure") == "identity":
            output["productId"] = "forged"
        return httpx.Response(
            200,
            json=dict(
                id="synthetic-content-result",
                model="synthetic-content-model",
                usage=dict(prompt_tokens=100, completion_tokens=20, total_tokens=120),
                choices=[dict(finish_reason="stop", message=dict(content=json.dumps(output)))],
            ),
        )

    runtime = ContentRuntime(
        ContentPort(
            "http://127.0.0.1:9009", allow_loopback=True, transport=httpx.MockTransport(handle)
        ),
        ServerSecrets({"CONTENT_TEST_KEY": SecretStr("synthetic-content-only-not-real")}),
    )
    spec = ModelSpec(
        model_key="synthetic.content",
        provider="deepseek",
        model="synthetic-content-model",
        endpoint_ref="CONTENT_TEST_ENDPOINT",
        secret_ref="CONTENT_TEST_KEY",
        billing_currency="CNY",
    )
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        s.execute(
            text(
                "INSERT INTO model_configs(tenant_id,capability_alias,revision,primary_spec) "
                "VALUES(:tenant,'content.copywriting',1,CAST(:spec AS jsonb))"
            ),
            dict(tenant=d["tenant"], spec=spec.model_dump_json()),
        )
        scenarios: list[Scenario] = ["content_planning", "content_script", "content_storyboard"]
        for scenario in scenarios:
            prompt = load_content_prompt(scenario)
            s.execute(
                text(
                    "INSERT INTO prompt_versions(tenant_id,prompt_key,version,capability_"
                    "alias,template,content_hash,output_schema_ref,status,created_by) "
                    "VALUES(:tenant,:key,1,'content.copywriting',:template,:hash,:schema,"
                    "'ACTIVE',:actor)"
                ),
                dict(
                    tenant=d["tenant"],
                    key=scenario,
                    template=prompt.template,
                    hash=prompt.digest,
                    schema=prompt.schema,
                    actor=d["identity"].user_id,
                ),
            )
        s.execute(
            text(
                "INSERT INTO ai_cost_budgets(tenant_id,scenario,currency,period_start,per"
                "iod_end,limit_minor,status) "
                "VALUES(:tenant,'copywriting','CNY',now()-interval '1 day',now()+interval"
                " '1 day',100,'ACTIVE')"
            ),
            dict(tenant=d["tenant"]),
        )
        s.execute(
            text(
                "INSERT INTO ai_price_versions(tenant_id,price_key,revision,kind,currency"
                ",unit,unit_scale,input_rate_micro_minor,"
                "output_rate_micro_minor,valid_from,valid_until,verification,proof_hash) "
                "VALUES(:tenant,'synthetic.content',1,'LLM','CNY',"
                "'TOKEN',1000000,1000000,1000000,now()-interval '1 day',now()+interval '1"
                " day','VERIFIED',:proof)"
            ),
            dict(tenant=d["tenant"], proof=hashlib.sha256(b"synthetic-not-real-price").hexdigest()),
        )
    d["client"].app.state.content_ai_runtime = runtime
    return d | dict(
        ai=AIContent(d["content"], runtime), runtime=runtime, calls=calls, control=control
    )


def start(
    d: dict[str, Any],
    scenario: Scenario = "content_planning",
    steps: list[dict[str, Any]] | None = None,
) -> tuple[WorkflowExecution, UUID, ContentPrepare]:
    code = {
        "content_planning": "planning",
        "content_script": "script",
        "content_storyboard": "storyboard",
    }[scenario]
    kind = "planning" if scenario == "content_planning" else "scripts"
    r, run = create(d, steps or [definition(code, kind="AI_ASSISTED", documentKind=kind)])
    root = UUID(r.get(run)["rootDocumentId"])
    return (
        r,
        run,
        ContentPrepare(
            requestKey="content-request",
            expectedRunVersion=1,
            expectedAttempt=1,
            documentId=str(root),
            documentVersion=1,
            aiScenario=scenario,
        ),
    )


def confirmation(prepared: dict[str, Any]) -> ContentConfirm:
    quote = prepared["quote"]
    return ContentConfirm(
        quoteHash=quote["hash"],
        maximumCostMinor=quote["maximumCostMinor"],
        currency=quote["currency"],
        confirmed=True,
    )


@pytest.mark.parametrize("scenario", ["content_planning", "content_script", "content_storyboard"])
def test_actual_model_producer_provenance_no_early_write_and_accept(
    ai_ready: dict[str, Any], scenario: Scenario
) -> None:
    d = ai_ready
    r, run, value = start(d, scenario)
    root = UUID(value.documentId)
    before = d["content"].get(root)
    prepared = d["ai"].prepare(
        run,
        {
            "content_planning": "planning",
            "content_script": "script",
            "content_storyboard": "storyboard",
        }[scenario],
        value,
    )
    assert prepared["state"] == "PREPARED" and d["calls"] == []
    result = d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert result["state"] == "CANDIDATE" and len(d["calls"]) == 1
    assert d["content"].get(root) == before
    assert (
        d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared)) == result
        and len(d["calls"]) == 1
    )
    assert (
        d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))["feeState"]
        == d["ai"].get(UUID(prepared["id"]))["feeState"]
    )
    current = r.get(run)
    code = current["steps"][0]["code"]
    accepted = r.accept(run, code, accept_value(current, code))
    assert accepted["status"] == "COMPLETED" and accepted["revision"] == 4
    with business_transaction(d["engine"], d["identity"]) as s:
        observed = s.execute(
            text(
                "SELECT c.producer,c.call_id,a.status,a.dispatch_state,a.cost_state FROM "
                "workflow_step_candidates c "
                "JOIN ai_invocations a ON (a.tenant_id,a.call_id,a.prompt_id,a.model_config_id)="
                "(c.tenant_id,c.call_id,c.prompt_id,c.model_config_id) WHERE c.id=:id"
            ),
            dict(id=UUID(result["candidateId"])),
        ).one()
        assert tuple(observed) == ("MODEL", UUID(result["callId"]), "OK", "RESPONDED", "UNKNOWN")
        assert (
            s.execute(
                text("SELECT state FROM ai_cost_permits WHERE request_key=:call"),
                dict(call=UUID(result["callId"])),
            ).scalar_one()
            == "UNKNOWN"
        )


def test_concurrent_confirmation_sends_once(ai_ready: dict[str, Any]) -> None:
    d = ai_ready
    r, run, value = start(d)
    prepared = d["ai"].prepare(run, "planning", value)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared)), range(2)
            )
        )
    assert len(d["calls"]) == 1 and all(x["state"] in {"IN_FLIGHT", "CANDIDATE"} for x in results)
    assert d["ai"].get(UUID(prepared["id"]))["state"] == "CANDIDATE" and r.get(run)["revision"] == 3


def test_late_reply_rejects_edited_document_and_keeps_real_cost(
    ai_ready: dict[str, Any],
) -> None:
    d = ai_ready
    r, run, value = start(d)
    prepared = d["ai"].prepare(run, "planning", value)
    root = UUID(value.documentId)

    def edit() -> None:
        d["content"].save(
            root, Document.model_validate(d["content"].get(root) | dict(body="人工新版本"))
        )

    d["control"]["on_send"] = edit
    result = d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert result["state"] == "STALE" and result["reason"] == "STALE_REPLY"
    assert (
        d["content"].get(root)["body"] == "人工新版本"
        and r.get(run)["steps"][0]["status"] == "FAILED"
    )
    assert r.get(run)["revision"] == 3 and r.get(run)["candidates"] == []
    assert len(d["calls"]) == 1


def test_revoked_scope_failure_finalizer_cannot_write_candidate(
    ai_ready: dict[str, Any],
) -> None:
    d = ai_ready
    r, run, value = start(d)
    prepared = d["ai"].prepare(run, "planning", value)

    def revoke() -> None:
        with d["maintenance"].begin() as s:
            s.execute(text("SET LOCAL ROLE app_owner"))
            s.execute(
                text("DELETE FROM role_grants WHERE membership_id=:member"),
                dict(member=d["identity"].membership_id),
            )

    d["control"]["on_send"] = revoke
    result = d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert result["state"] == "STALE" and result["reason"] == "SCOPE_CHANGED"
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            s.execute(
                text("SELECT count(*) FROM workflow_step_candidates WHERE run_id=:run"),
                dict(run=run),
            ).scalar_one()
            == 0
        )
        assert (
            s.execute(
                text("SELECT status FROM workflow_run_steps WHERE run_id=:run"), dict(run=run)
            ).scalar_one()
            == "FAILED"
        )
        assert (
            s.execute(
                text("SELECT revision FROM workflow_runs WHERE id=:run"), dict(run=run)
            ).scalar_one()
            == 3
        )
        assert (
            s.execute(
                text(
                    "SELECT result->'candidates' FROM workflow_step_actions "
                    "WHERE run_id=:run AND action='model_failure'"
                ),
                dict(run=run),
            ).scalar_one()
            == []
        )
    with pytest.raises(ApiError):
        r.get(run)


@pytest.mark.parametrize("failure", [429, 503, "identity", "timeout"])
def test_model_failures_are_explicit_never_retry_or_write(
    ai_ready: dict[str, Any], failure: Any
) -> None:
    d = ai_ready
    r, run, value = start(d)
    prepared = d["ai"].prepare(run, "planning", value)
    d["control"]["failure"] = failure
    result = d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert result["state"] in {"FAILED", "UNKNOWN"} and result["candidateId"] is None
    assert r.get(run)["steps"][0]["status"] == "FAILED" and r.get(run)["revision"] == 3
    d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert len(d["calls"]) == 1


def test_sensitive_context_and_changed_quote_fail_before_network(
    ai_ready: dict[str, Any],
) -> None:
    d = ai_ready
    r, run, value = start(d)
    root = UUID(value.documentId)
    d["content"].save(
        root, Document.model_validate(d["content"].get(root) | dict(body="联系人13812345678"))
    )
    with pytest.raises(ApiError):
        d["ai"].prepare(run, "planning", value.model_copy(update={"documentVersion": 2}))
    assert d["calls"] == []
    d["content"].save(root, Document.model_validate(d["content"].get(root) | dict(body="安全文本")))
    prepared = d["ai"].prepare(run, "planning", value.model_copy(update={"documentVersion": 3}))
    with pytest.raises(ApiError):
        d["ai"].confirm(
            UUID(prepared["id"]),
            confirmation(prepared).model_copy(update={"maximumCostMinor": 999}),
        )
    assert d["calls"] == [] and r.get(run)["revision"] == 1


def test_real_http_and_default_disabled(ai_ready: dict[str, Any]) -> None:
    d = ai_ready
    r, run, value = start(d)
    client = d["client"]
    client.cookies.clear()
    sign_in(d)
    headers = {"X-Shop-Id": str(d["shop"]), "X-CSRF-Token": challenge(client)}
    url = f"/v1/content/workflow-runs/{run}/steps/planning/ai/prepare"
    client.app.state.content_ai_runtime = None
    assert client.post(url, json=value.model_dump(), headers=headers).status_code == 422
    client.app.state.content_ai_runtime = d["runtime"]
    reply = client.post(url, json=value.model_dump(), headers=headers)
    assert reply.status_code == 200, reply.text
    prepared = reply.json()["data"]
    assert (
        client.post(
            "/v1/content/ai-requests/" + prepared["id"] + "/confirm",
            json=confirmation(prepared).model_dump(),
            headers=headers,
        ).json()["data"]["state"]
        == "CANDIDATE"
    )
    assert len(d["calls"]) == 1


def teammate(d: dict[str, Any]) -> Any:
    from yuliang.platform.db import VerifiedIdentity

    member, user = uuid4(), uuid4()
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        s.execute(
            text("INSERT INTO users(id,email,display_name) VALUES(:id,:email,'合成同事')"),
            dict(id=user, email=str(user) + "@example.invalid"),
        )
        s.execute(
            text("INSERT INTO memberships(id,tenant_id,user_id) VALUES(:id,:tenant,:user)"),
            dict(id=member, tenant=d["tenant"], user=user),
        )
        s.execute(
            text(
                "INSERT INTO role_grants(tenant_id,membership_id,role_id) SELECT tenant_i"
                "d,:new,role_id FROM role_grants WHERE membership_id=:old"
            ),
            dict(new=member, old=d["identity"].membership_id),
        )
        s.execute(
            text(
                "INSERT INTO data_scopes(tenant_id,membership_id,scope_type,entity_id,sho"
                "p_id,account_id,permissions_mask) "
                "SELECT tenant_id,:new,scope_type,entity_id,shop_id,account_id,permission"
                "s_mask FROM data_scopes WHERE membership_id=:old"
            ),
            dict(new=member, old=d["identity"].membership_id),
        )
    return VerifiedIdentity(d["tenant"], member, user)


def test_cross_actor_inflight_completion_slot_cannot_be_taken_by_manual(
    ai_ready: dict[str, Any],
) -> None:
    from yuliang.modules.content.service import ContentService
    from yuliang.modules.content.workflows_execution_models import Execute
    from yuliang.modules.content.workflows_execution_service import WorkflowExecution

    d = ai_ready
    r, run, value = start(
        d, steps=[definition("planning", kind="AI_ASSISTED"), definition("manual")]
    )
    second = teammate(d)
    other = WorkflowExecution(ContentService(d["engine"], second, d["shop"]))
    prepared = d["ai"].prepare(run, "planning", value)
    # 合成配额负载；并不声称实际完成998次业务动作。
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        s.execute(
            text(
                "INSERT INTO workflow_step_actions(tenant_id,run_id,action_key,step_code,"
                "action,request_hash,result,created_by) "
                "SELECT :tenant,:run,'quota-'||i,'planning','redo',repeat('a',64),"
                "jsonb_build_object('revision',1,'steps','[]'::jsonb),:actor FROM generat"
                "e_series(1,998) AS i"
            ),
            dict(tenant=d["tenant"], run=run, actor=d["identity"].user_id),
        )
    blocked = []

    def while_sending() -> None:
        with business_transaction(d["engine"], second) as s:
            assert (
                s.execute(
                    text("SELECT count(*) FROM content_ai_requests WHERE run_id=:run"),
                    dict(run=run),
                ).scalar_one()
                == 0
            )
            assert (
                s.execute(
                    text("SELECT access.workflow_action_occupancy(:run)"), dict(run=run)
                ).scalar_one()
                == 1000
            )
        with pytest.raises(ApiError) as error:
            other.execute(
                run,
                "manual",
                Execute(
                    actionKey="steal-slot",
                    expectedRunVersion=2,
                    expectedAttempt=1,
                    documentId=value.documentId,
                    documentVersion=1,
                    patch={"body": "不能抢占"},
                ),
            )
        assert error.value.code == ErrorCode.INVALID_INPUT
        blocked.append(True)

    d["control"]["on_send"] = while_sending
    result = d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert result["state"] == "CANDIDATE" and blocked == [True] and len(d["calls"]) == 1
    assert len(r.get(run)["history"]) == 1000


def test_insufficient_budget_and_forged_failure_finalizer_are_not_calls(
    ai_ready: dict[str, Any],
) -> None:
    d = ai_ready
    r, run, value = start(d)
    prepared = d["ai"].prepare(run, "planning", value)
    second = teammate(d)
    with pytest.raises(DBAPIError), business_transaction(d["engine"], d["identity"]) as s:
        s.execute(text("SELECT access.content_ai_failure(:id,NULL)"), dict(id=UUID(prepared["id"])))
    with business_transaction(d["engine"], second) as s:
        assert not s.execute(
            text("SELECT access.content_ai_failure(:id,'RESULT_UNKNOWN')"),
            dict(id=UUID(prepared["id"])),
        ).scalar_one()
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        s.execute(
            text("UPDATE ai_cost_budgets SET limit_minor=0 WHERE tenant_id=:tenant"),
            dict(tenant=d["tenant"]),
        )
    with pytest.raises(ApiError):
        d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert d["calls"] == [] and r.get(run)["revision"] == 1
    assert d["ai"].get(UUID(prepared["id"]))["state"] == "PREPARED"


def test_real_asset_context_fixed_version_and_inflight_archive_guard(
    ai_ready: dict[str, Any], assets_ready: dict[str, Any]
) -> None:
    d = ai_ready | dict(content=assets_ready["docs"])
    d["ai"] = AIContent(d["content"], d["runtime"])
    r, run, value = start(d)
    value = value.model_copy(update={"assetIds": [str(assets_ready["asset"])]})
    prepared = d["ai"].prepare(run, "planning", value)
    blocked = []

    def archive_during_call() -> None:
        with pytest.raises(ApiError):
            assets_ready["assets"].archive(assets_ready["asset"])
        blocked.append(True)

    d["control"]["on_send"] = archive_during_call
    result = d["ai"].confirm(UUID(prepared["id"]), confirmation(prepared))
    assert result["state"] == "CANDIDATE" and blocked == [True]
    outgoing = json.loads(d["calls"][0]["messages"][1]["content"])
    assert len(outgoing["assets"]) == 1 and set(outgoing["assets"][0]) == {"kind", "name"}
    assert str(assets_ready["asset"]) not in json.dumps(outgoing)
    with business_transaction(d["engine"], d["identity"]) as s:
        facts = s.execute(
            text("SELECT asset_snapshots FROM content_ai_requests WHERE id=:id"),
            dict(id=UUID(prepared["id"])),
        ).scalar_one()
        assert (
            facts[0]["version"]
            and facts[0]["sha256"] == hashlib.sha256(assets_ready["asset_body"]).hexdigest()
        )
    assets_ready["assets"].archive(assets_ready["asset"])
    _, another, newvalue = start(d)
    with pytest.raises(ApiError):
        d["ai"].prepare(
            another,
            "planning",
            newvalue.model_copy(update={"assetIds": [str(assets_ready["asset"])]}),
        )
    assert len(d["calls"]) == 1


def test_private_cross_actor_prepare_key_conflict_is_controlled_and_rolls_back(
    ai_ready: dict[str, Any],
) -> None:
    from yuliang.modules.content.service import ContentService

    d = ai_ready
    _, run, value = start(d)
    prepared = d["ai"].prepare(run, "planning", value)
    second = teammate(d)
    other = AIContent(ContentService(d["engine"], second, d["shop"]), d["runtime"])
    with pytest.raises(ApiError) as conflict:
        other.prepare(run, "planning", value)
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_MISMATCH
    assert prepared["id"] not in conflict.value.message and d["calls"] == []
    with business_transaction(d["engine"], second) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_ai_requests WHERE run_id=:run"), dict(run=run)
            ).scalar_one()
            == 0
        )
        assert (
            s.execute(
                text("SELECT count(*) FROM ai_invocations WHERE user_id=:actor"),
                dict(actor=second.user_id),
            ).scalar_one()
            == 0
        )
        assert (
            s.execute(
                text("SELECT count(*) FROM ai_cost_quotes WHERE actor_id=:actor"),
                dict(actor=second.user_id),
            ).scalar_one()
            == 0
        )
    assert d["ai"].get(UUID(prepared["id"]))["state"] == "PREPARED"


def test_shared_asset_budget_reply_takes_business_locks_before_fees(
    ai_ready: dict[str, Any], assets_ready: dict[str, Any], monkeypatch: Any
) -> None:
    from threading import Event

    d = ai_ready | dict(content=assets_ready["docs"])
    first = AIContent(d["content"], d["runtime"])
    second = AIContent(d["content"], d["runtime"])
    _, run_a, value_a = start(d)
    _, run_b, value_b = start(d)
    asset = str(assets_ready["asset"])
    prepared_a = first.prepare(run_a, "planning", value_a.model_copy(update={"assetIds": [asset]}))
    prepared_b = second.prepare(run_b, "planning", value_b.model_copy(update={"assetIds": [asset]}))
    a_sending = Event()
    allow_reply = Event()
    b_has_business_locks = Event()
    allow_b_fee = Event()
    a_reply_locking = Event()
    a_fee = Event()

    def on_send() -> None:
        if len(d["calls"]) == 1:
            a_sending.set()
            assert allow_reply.wait(4)

    d["control"]["on_send"] = on_send
    original_acquire = second.gate.acquire_in_transaction

    def acquire_b(*args: Any, **kwargs: Any) -> Any:
        b_has_business_locks.set()
        assert allow_b_fee.wait(4)
        return original_acquire(*args, **kwargs)

    monkeypatch.setattr(second.gate, "acquire_in_transaction", acquire_b)
    original_locks = first._business_locks
    lock_calls = []

    def locks_a(*args: Any, **kwargs: Any) -> None:
        lock_calls.append(True)
        if len(lock_calls) == 2:
            a_reply_locking.set()
        original_locks(*args, **kwargs)

    monkeypatch.setattr(first, "_business_locks", locks_a)
    original_finish = first.gate.finish_in_transaction

    def finish_a(*args: Any, **kwargs: Any) -> Any:
        a_fee.set()
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(first.gate, "finish_in_transaction", finish_a)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first.confirm, UUID(prepared_a["id"]), confirmation(prepared_a))
        assert a_sending.wait(4)
        b = pool.submit(second.confirm, UUID(prepared_b["id"]), confirmation(prepared_b))
        try:
            assert b_has_business_locks.wait(4)
            with d["maintenance"].begin() as s:
                assert not s.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,760159))"),
                    dict(key=f"{d['tenant']}|{asset}"),
                ).scalar_one()
            allow_reply.set()
            assert a_reply_locking.wait(4)
            assert not a_fee.wait(0.1)
        finally:
            allow_b_fee.set()
            allow_reply.set()
        assert a.result(timeout=12)["state"] == "CANDIDATE"
        assert b.result(timeout=12)["state"] == "CANDIDATE"
    assert len(d["calls"]) == 2 and a_fee.is_set()
