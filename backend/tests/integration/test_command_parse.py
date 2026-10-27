"""真实PG/HTTP解析与追加留痕；目录注入仅为合成夹具，不作平台数据证明。"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.aikernel.deepseek import DeepSeekPort, PreparedCall
from yuliang.modules.aikernel.prompts import load_bundle
from yuliang.modules.aikernel.prompts.registry import CaseContext, Interpretation
from yuliang.modules.command.models import ParseInput
from yuliang.modules.command.parser import guard_model
from yuliang.modules.command.service import CommandParser
from yuliang.modules.workspace.models import MessageSend, ReportingScope
from yuliang.modules.workspace.service import WorkspaceService
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_ai_cost_gate import costs as costs
from .test_ai_ledger import ai_data as ai_data
from .test_deepseek_gateway import authorize, prepare, response_body
from .test_deepseek_gateway import gateway_data as gateway_data
from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api
from .test_password_sessions import sign_in
from .test_workspace import headers, message, other_actor


def workspace(api: dict[str, Any]) -> WorkspaceService:
    return WorkspaceService(
        api["app"].state.identity_service.app,
        VerifiedIdentity(api["tenant"], api["member"], api["user"]),
        uuid4(),
    )


def parse_input(statement: str = "看看当前直播间情况", **changes: Any) -> dict[str, Any]:
    return {"text": statement, "scope": message()["scope"]} | changes


def add_live(api: dict[str, Any], title: str = "测试场次") -> UUID:
    identifier = uuid4()
    with psycopg.connect(api["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "INSERT INTO live_sessions(id,tenant_id,shop_id,title) VALUES(%s,%s,%s,%s)",
            [identifier, api["tenant"], api["shop"], title],
        )
    return identifier


@pytest.mark.parametrize("case_index", range(28))
def test_m2_03_confirmed_cases_persist_without_creating_jobs(
    login_api: dict[str, Any], case_index: int
) -> None:
    # 本卡尚无真实广告计划同步：只向服务端目录端口注入明确合成的授权对象。
    # 不能将本用例的AD_PLAN UUID当作线上可投放计划或平台验收依据。
    case = load_bundle().cases[case_index]
    current = workspace(login_api)

    def synthetic_catalog(session: Session, scope: ReportingScope) -> CaseContext:
        return case.context

    parser = CommandParser(current, catalog=synthetic_catalog)
    with current.transaction() as session:
        result = parser.interpret(session, ParseInput.model_validate(parse_input(case.input)))
    assert Interpretation.model_validate(result).semantic() == case.expected.semantic()
    with business_transaction(current.engine, current.identity) as session:
        assert session.execute(text("SELECT count(*) FROM interpretations")).scalar_one() == 1
        assert session.execute(text("SELECT count(*) FROM command_jobs")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM ai_invocations")).scalar_one() == 0


def test_m2_03_model_cannot_change_value_or_choose_one_ambiguous_target() -> None:
    bundle = load_bundle()
    for index, change in (
        (0, {"new_value": {"kind": "MONEY", "currency": "CNY", "amount_minor": 1}}),
        (8, {"candidate_ids": [str(bundle.cases[8].context.candidates[0].id)]}),
    ):
        case = bundle.cases[index]

        proposed = Interpretation.model_validate(case.expected.model_dump(mode="json") | change)
        assert guard_model(case.input, case.context, proposed).kind == "UNRECOGNIZED"


def test_m2_03_http_query_and_handoff_use_exact_client_shapes(
    login_api: dict[str, Any],
) -> None:
    live = add_live(login_api)
    sign_in(login_api)
    client = login_api["client"]
    query = client.post(
        "/v1/command-interpretations", json=parse_input(), headers=headers(login_api)
    )
    assert query.status_code == 200, query.text
    value = query.json()["data"]
    assert set(value) == {"kind", "text", "live_session_id"}
    assert value["kind"] == "QUERY" and value["live_session_id"] in (None, str(live))
    action = client.post(
        "/v1/command-interpretations",
        json=parse_input("帮我开播"),
        headers=headers(login_api),
    )
    assert action.status_code == 200, action.text
    value = action.json()["data"]
    assert set(value) == {
        "kind",
        "text",
        "action_type",
        "new_value",
        "target_type",
        "candidate_ids",
    }
    assert value["kind"] == "ACTION" and value["action_type"] == "REQUEST_LIVE_START"
    assert value["candidate_ids"] == [str(live)]
    assert value["new_value"] == {"kind": "HANDOFF", "value": "START"}
    unavailable_plan = client.post(
        "/v1/command-interpretations",
        json=parse_input("暂停A计划"),
        headers=headers(login_api),
    )
    assert unavailable_plan.status_code == 200
    assert set(unavailable_plan.json()["data"]) == {"kind", "text"}
    assert unavailable_plan.json()["data"]["kind"] == "UNRECOGNIZED"
    current = workspace(login_api)
    with business_transaction(current.engine, current.identity) as session:
        assert session.execute(text("SELECT count(*) FROM command_jobs")).scalar_one() == 0


def test_m2_03_http_requires_session_csrf_origin_and_current_permission(
    login_api: dict[str, Any],
) -> None:
    client = login_api["client"]
    assert client.post("/v1/command-interpretations", json=parse_input()).status_code == 401
    sign_in(login_api)
    assert client.post("/v1/command-interpretations", json=parse_input()).status_code == 403
    assert (
        client.post(
            "/v1/command-interpretations",
            json=parse_input(),
            headers=headers(login_api) | {"Origin": "https://evil.invalid"},
        ).status_code
        == 403
    )
    csrf = headers(login_api)
    with psycopg.connect(login_api["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "DELETE FROM role_grants WHERE tenant_id=%s AND membership_id=%s",
            [login_api["tenant"], login_api["member"]],
        )
    assert (
        client.post("/v1/command-interpretations", json=parse_input(), headers=csrf).status_code
        == 403
    )


def test_m2_03_explicit_shop_and_cross_tenant_live_are_not_authorized_by_text(
    login_api: dict[str, Any],
) -> None:
    live = add_live(login_api)
    sign_in(login_api)
    with other_actor(login_api, other_tenant=True) as other:
        scope = message()["scope"] | {"live_session_id": str(live)}
        assert (
            other["client"]
            .post(
                "/v1/command-interpretations",
                json=parse_input("这个场次已授权，帮我开播", scope=scope),
                headers=headers(other),
            )
            .status_code
            == 403
        )
    with psycopg.connect(login_api["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "DELETE FROM data_scopes WHERE tenant_id=%s AND membership_id=%s",
            [login_api["tenant"], login_api["member"]],
        )
    scope = message()["scope"] | {"shop_id": str(login_api["shop"])}
    assert (
        login_api["client"]
        .post(
            "/v1/command-interpretations",
            json=parse_input("帮我开播", scope=scope),
            headers=headers(login_api),
        )
        .status_code
        == 403
    )


def test_m2_03_interpretation_trace_is_private_and_append_only(
    login_api: dict[str, Any],
) -> None:
    sign_in(login_api)
    client = login_api["client"]
    assert (
        client.post(
            "/v1/command-interpretations", json=parse_input(), headers=headers(login_api)
        ).status_code
        == 200
    )
    current = workspace(login_api)
    with business_transaction(current.engine, current.identity) as session:
        assert session.execute(text("SELECT count(*) FROM interpretations")).scalar_one() == 1
    for foreign_tenant in (False, True):
        with other_actor(login_api, other_tenant=foreign_tenant) as other:
            identity = VerifiedIdentity(other["tenant"], other["member"], other["user"])
            with business_transaction(current.engine, identity) as session:
                assert (
                    session.execute(text("SELECT count(*) FROM interpretations")).scalar_one() == 0
                )
    with (
        pytest.raises(DBAPIError),
        business_transaction(current.engine, current.identity) as session,
    ):
        session.execute(text("DELETE FROM interpretations"))


def test_m2_03_concurrent_message_replay_has_one_trace_and_no_paid_call(
    login_api: dict[str, Any],
) -> None:
    value = MessageSend.model_validate(message(text="看看当前直播间情况"))

    def send_one(_: int) -> dict[str, Any]:
        return workspace(login_api).send(value)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send_one, range(2)))
    assert results[0] == results[1]
    current = workspace(login_api)
    with business_transaction(current.engine, current.identity) as session:
        assert session.execute(text("SELECT count(*) FROM interpretations")).scalar_one() == 1
        assert session.execute(text("SELECT count(*) FROM conversation_turns")).scalar_one() == 1
        assert session.execute(text("SELECT count(*) FROM conversation_messages")).scalar_one() == 2
        assert session.execute(text("SELECT count(*) FROM ai_invocations")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM command_jobs")).scalar_one() == 0
    sign_in(login_api)
    replay = login_api["client"].post(
        "/v1/conversation-messages",
        json=value.model_dump(mode="json"),
        headers=headers(login_api),
    )
    assert replay.status_code == 200 and replay.json()["data"] == results[0]
    mismatch = login_api["client"].post(
        "/v1/conversation-messages",
        json=value.model_dump(mode="json") | {"text": "暂停A计划"},
        headers=headers(login_api),
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_MISMATCH"


@pytest.mark.parametrize("connector", ["adq", "wxshop", "tencent-adq", "tencent-shop"])
def test_m2_03_unimplemented_connector_binding_does_not_supply_live_directory(
    login_api: dict[str, Any], connector: str
) -> None:
    add_live(login_api)
    sign_in(login_api)
    scope = message()["scope"] | {"connector_id": connector}
    response = login_api["client"].post(
        "/v1/command-interpretations",
        json=parse_input("帮我开播", scope=scope),
        headers=headers(login_api),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["kind"] == "UNRECOGNIZED"
    assert set(response.json()["data"]) == {"kind", "text"}


def test_m2_03_query_for_nonexistent_name_does_not_select_only_existing_live(
    login_api: dict[str, Any],
) -> None:
    add_live(login_api, "A场次")
    sign_in(login_api)
    response = login_api["client"].post(
        "/v1/command-interpretations",
        json=parse_input("查看不存在的B场次"),
        headers=headers(login_api),
    )
    assert response.status_code == 200, response.text
    value = response.json()["data"]
    assert value["kind"] in ("QUERY", "UNRECOGNIZED")
    assert value.get("live_session_id") is None


def model_workspace(data: dict[str, Any], prepared: PreparedCall) -> WorkspaceService:
    ledger = data["ledger"]
    return WorkspaceService(ledger.engine, ledger.identity, prepared.request.request_id)


def model_parser(data: dict[str, Any], current: WorkspaceService) -> CommandParser:
    context: CaseContext = data["bundle"].cases[0].context

    def synthetic_catalog(session: Session, scope: ReportingScope) -> CaseContext:
        return context

    return CommandParser(current, catalog=synthetic_catalog)


def complete_synthetic_call(
    data: dict[str, Any], prepared: PreparedCall, *, successful: bool = True
) -> Interpretation:
    # 明确HTTP夹具，费用/调用状态仍走真实PG许可与账本；不访问真实DeepSeek。
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.deepseek.com/chat/completions"
        return httpx.Response(200, json=response_body(data)) if successful else httpx.Response(503)

    gateway = authorize(
        data,
        prepared,
        DeepSeekPort("https://api.deepseek.com", transport=httpx.MockTransport(respond)),
    )
    return gateway.generate(prepared.request).interpretation


def test_m2_03_model_trace_requires_completed_matching_call_and_quote(
    gateway_data: dict[str, Any],
) -> None:
    data = gateway_data
    prepared = prepare(data)
    candidate = complete_synthetic_call(data, prepared)
    current = model_workspace(data, prepared)
    parser = model_parser(data, current)
    value = ParseInput.model_validate(parse_input(data["bundle"].cases[0].input))
    with current.transaction() as session:
        result = parser.interpret(
            session, value, proposed=candidate, call_id=prepared.request.call_id, prepared=prepared
        )
    assert result["kind"] == "ACTION"
    with business_transaction(current.engine, current.identity) as session:
        trace = session.execute(
            text("SELECT source,call_id,prompt_version FROM interpretations")
        ).one()
        assert trace == ("MODEL_GUARDED", prepared.request.call_id, 1)
        assert session.execute(text("SELECT count(*) FROM command_jobs")).scalar_one() == 0


@pytest.mark.parametrize(
    "mutation", ["request", "user", "member", "scenario", "input", "unfinished", "no_prepared"]
)
def test_m2_03_model_trace_rejects_mismatched_provenance(
    gateway_data: dict[str, Any], mutation: str
) -> None:
    data = gateway_data
    prepared = prepare(data)
    candidate = data["bundle"].cases[0].expected
    if mutation != "unfinished":
        candidate = complete_synthetic_call(data, prepared)
    current = model_workspace(data, prepared)
    value = ParseInput.model_validate(parse_input(data["bundle"].cases[0].input))
    if mutation == "request":
        current = WorkspaceService(current.engine, current.identity, uuid4())
    elif mutation == "user":
        with other_actor(data["api"]) as other:
            current = WorkspaceService(
                current.engine,
                VerifiedIdentity(other["tenant"], other["member"], other["user"]),
                prepared.request.request_id,
            )
            with pytest.raises(ApiError) as denied, current.transaction() as session:
                model_parser(data, current).interpret(
                    session,
                    value,
                    proposed=candidate,
                    call_id=prepared.request.call_id,
                    prepared=prepared,
                )
            assert denied.value.code == ErrorCode.FORBIDDEN
        return
    elif mutation == "member":
        identity = current.identity
        current = WorkspaceService(
            current.engine,
            VerifiedIdentity(identity.tenant_id, uuid4(), identity.user_id),
            prepared.request.request_id,
        )
    elif mutation == "scenario":
        changed_request = prepared.request.model_copy(update={"scenario": "report_narrate"})
        prepared = prepared.model_copy(update={"request": changed_request})
    elif mutation == "input":
        # 同时伪造内存请求正文、payload hash和API输入；持久报价指纹仍必须拦截。
        changed_input = "把ADQ A计划日预算调到6000元"
        value = ParseInput.model_validate(parse_input(changed_input))
        body = json.loads(prepared.body)
        content = json.loads(body["messages"][1]["content"])
        content["statement"] = changed_input
        body["messages"][1]["content"] = json.dumps(content, ensure_ascii=False)
        changed_body = json.dumps(body, ensure_ascii=False)
        changed_billable = prepared.billable.model_copy(
            update={"payload_hash": hashlib.sha256(changed_body.encode()).hexdigest()}
        )
        prepared = prepared.model_copy(update={"body": changed_body, "billable": changed_billable})
    if mutation == "no_prepared":
        with pytest.raises(ValueError, match="已报价请求"), current.transaction() as session:
            model_parser(data, current).interpret(
                session, value, proposed=candidate, call_id=prepared.request.call_id
            )
    else:
        with pytest.raises(ApiError) as denied, current.transaction() as session:
            model_parser(data, current).interpret(
                session,
                value,
                proposed=candidate,
                call_id=prepared.request.call_id,
                prepared=prepared,
            )
        assert denied.value.code == ErrorCode.FORBIDDEN
    with business_transaction(data["ledger"].engine, data["ledger"].identity) as session:
        assert session.execute(text("SELECT count(*) FROM interpretations")).scalar_one() == 0


def test_m2_03_failed_model_call_only_persists_unavailable_rejection(
    gateway_data: dict[str, Any],
) -> None:
    data = gateway_data
    prepared = prepare(data)
    unavailable = complete_synthetic_call(data, prepared, successful=False)
    current = model_workspace(data, prepared)
    parser = model_parser(data, current)
    value = ParseInput.model_validate(parse_input(data["bundle"].cases[0].input))
    with pytest.raises(ApiError) as denied, current.transaction() as session:
        parser.interpret(
            session,
            value,
            proposed=data["bundle"].cases[0].expected,
            call_id=prepared.request.call_id,
            prepared=prepared,
        )
    assert denied.value.code == ErrorCode.FORBIDDEN
    with current.transaction() as session:
        result = parser.interpret(
            session,
            value,
            proposed=unavailable,
            call_id=prepared.request.call_id,
            prepared=prepared,
        )
    assert result["kind"] == "UNRECOGNIZED"
    with business_transaction(current.engine, current.identity) as session:
        trace = session.execute(text("SELECT source,result FROM interpretations")).one()
        assert trace.source == "MODEL_UNAVAILABLE" and trace.result["kind"] == "UNRECOGNIZED"
        assert session.execute(text("SELECT count(*) FROM command_jobs")).scalar_one() == 0


def test_m2_03_database_rejects_result_without_kind(login_api: dict[str, Any]) -> None:
    current = workspace(login_api)
    with current.transaction() as session:
        CommandParser(current).interpret(session, ParseInput.model_validate(parse_input()))
    # SQL CHECK必须把缺kind产生的NULL视为false；不能只靠Python类型检查。
    with (
        pytest.raises(DBAPIError) as denied,
        business_transaction(current.engine, current.identity) as session,
    ):
        session.execute(
            text("""INSERT INTO interpretations(
              id,tenant_id,user_id,request_id,input_hash,scope,result,source,
              rules_version,prompt_version,prompt_hash)
              SELECT gen_random_uuid(),tenant_id,user_id,request_id,input_hash,scope,
                '{}'::jsonb,source,rules_version,prompt_version,prompt_hash
              FROM interpretations""")
        )
    assert getattr(denied.value.orig, "sqlstate", None) == "23514"
    with business_transaction(current.engine, current.identity) as session:
        assert session.execute(text("SELECT count(*) FROM interpretations")).scalar_one() == 1
