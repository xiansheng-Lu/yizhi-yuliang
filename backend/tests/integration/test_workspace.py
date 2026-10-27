"""真实PG与HTTP的会话/项目持久化、幂等、私有RLS和任务关联；不伪造解析验收。"""

import json
import secrets
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.main import create_app
from yuliang.modules.identity.service import HASHER
from yuliang.modules.workspace.models import MessageSend
from yuliang.modules.workspace.service import WorkspaceService
from yuliang.platform.db import VerifiedIdentity, business_transaction

from .test_identity_database import database as database
from .test_password_sessions import challenge, sign_in
from .test_password_sessions import login_api as login_api


def message(**changes: Any) -> dict[str, Any]:
    return {
        "text": "帮我看看当前情况",
        "source": "PC_MANUAL",
        "scope": {
            "shop_id": "all",
            "live_session_id": "all",
            "connector_id": "all",
            "date": "2026-10-27",
            "timezone": "Asia/Shanghai",
        },
        "idempotencyKey": str(uuid4()),
    } | changes


def headers(api: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": challenge(api["client"])}


def sent(api: dict[str, Any], **changes: Any) -> dict[str, Any]:
    response = api["client"].post(
        "/v1/conversation-messages", json=message(**changes), headers=headers(api)
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


@contextmanager
def other_actor(base: dict[str, Any], *, other_tenant: bool = False) -> Iterator[dict[str, Any]]:
    user, member = uuid4(), uuid4()
    email, password = f"{user}@example.invalid", secrets.token_urlsafe(24)
    with psycopg.connect(base["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        tenant = base["tenant"]
        if other_tenant:
            created = control.execute(
                "INSERT INTO tenants(name) VALUES('Other workspace') RETURNING id"
            ).fetchone()
            assert created
            tenant = created[0]
            control.execute("SELECT access.seed_tenant_roles(%s)", [tenant])
        control.execute(
            "INSERT INTO users(id,email,password_hash,display_name) VALUES(%s,%s,%s,'Other')",
            [user, email, HASHER.hash(password)],
        )
        control.execute(
            "INSERT INTO memberships(id,tenant_id,user_id) VALUES(%s,%s,%s)", [member, tenant, user]
        )
        role = control.execute(
            "SELECT id FROM roles WHERE tenant_id=%s AND code='OPERATOR'", [tenant]
        ).fetchone()
        assert role
        control.execute(
            "INSERT INTO role_grants(tenant_id,membership_id,role_id) VALUES(%s,%s,%s)",
            [tenant, member, role[0]],
        )
        control.execute(
            "INSERT INTO data_scopes(tenant_id,membership_id,scope_type) "
            "VALUES(%s,%s,'ALL_TENANT')",
            [tenant, member],
        )
    with TestClient(create_app(base["settings"]), raise_server_exceptions=False) as client:
        api = base | {
            "client": client,
            "user": user,
            "member": member,
            "tenant": tenant,
            "email": email,
            "password": password,
        }
        sign_in(api)
        yield api


def test_m2_02_project_message_and_cross_device_persistence(login_api: dict[str, Any]) -> None:
    sign_in(login_api)
    client = login_api["client"]
    assert client.get("/v1/workspace/history").json()["data"] == {
        "projects": [],
        "conversations": [],
        "taskProjects": {},
    }
    project = client.post(
        "/v1/projects", json={"name": "  我的项目  "}, headers=headers(login_api)
    ).json()["data"]
    assert project["name"] == "我的项目"
    turn = sent(login_api, projectId=project["id"])
    assert [item["role"] for item in turn["conversation"]["messages"]] == ["user", "assistant"]
    assistant = turn["conversation"]["messages"][-1]
    assert (
        assistant["id"] == turn["messageId"]
        and assistant["interpretation"] == turn["interpretation"]
    )
    assert assistant["scope"] == message()["scope"] and turn["interpretation"]["kind"] == "QUERY"
    # 独立客户端重新实际登录，不能靠原进程内存或复用原Cookie冒充跨设备。
    with TestClient(create_app(login_api["settings"]), raise_server_exceptions=False) as second:
        sign_in(login_api | {"client": second})
        snapshot = second.get("/v1/workspace/history").json()["data"]
        assert snapshot["projects"] == [project] and snapshot["conversations"] == [
            turn["conversation"]
        ]


def test_m2_02_idempotent_turn_is_stable_and_changed_payload_conflicts(
    login_api: dict[str, Any],
) -> None:
    sign_in(login_api)
    value = message(text="  同一条消息  ")
    client = login_api["client"]
    csrf = headers(login_api)
    first = client.post("/v1/conversation-messages", json=value, headers=csrf)
    assert first.status_code == 200, first.text
    assert (
        client.post(
            "/v1/conversation-messages", json=value | {"text": "同一条消息"}, headers=csrf
        ).json()["data"]
        == first.json()["data"]
    )
    mismatch = client.post(
        "/v1/conversation-messages", json=value | {"text": "其他消息"}, headers=csrf
    )
    assert (
        mismatch.status_code == 409 and mismatch.json()["error"]["code"] == "IDEMPOTENCY_MISMATCH"
    )
    conversation = first.json()["data"]["conversation"]
    sent(login_api, conversationId=conversation["id"], text="后续消息")
    replay = client.post("/v1/conversation-messages", json=value, headers=headers(login_api))
    assert replay.json()["data"] == first.json()["data"]
    assert (
        len(client.get("/v1/workspace/history").json()["data"]["conversations"][0]["messages"]) == 4
    )


def test_m2_02_concurrent_same_key_commits_only_one_turn(login_api: dict[str, Any]) -> None:
    identity = VerifiedIdentity(login_api["tenant"], login_api["member"], login_api["user"])
    engine = login_api["app"].state.identity_service.app
    value = MessageSend.model_validate(message())

    def send_one(_: int) -> dict[str, Any]:
        return WorkspaceService(engine, identity, uuid4()).send(value)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send_one, range(2)))
    assert results[0] == results[1]
    with business_transaction(engine, identity) as session:
        assert session.execute(text("SELECT count(*) FROM conversation_turns")).scalar_one() == 1
        assert session.execute(text("SELECT count(*) FROM conversation_messages")).scalar_one() == 2


@pytest.mark.parametrize("other_tenant", [False, True])
def test_m2_02_other_user_or_tenant_cannot_read_move_or_bind(
    login_api: dict[str, Any], other_tenant: bool
) -> None:
    sign_in(login_api)
    project = (
        login_api["client"]
        .post("/v1/projects", json={"name": "私有"}, headers=headers(login_api))
        .json()["data"]
    )
    turn = sent(login_api, projectId=project["id"])
    with other_actor(login_api, other_tenant=other_tenant) as other:
        client = other["client"]
        assert client.get("/v1/workspace/history").json()["data"] == {
            "projects": [],
            "conversations": [],
            "taskProjects": {},
        }
        csrf = headers(other)
        assert (
            client.patch(
                f"/v1/conversations/{turn['conversation']['id']}",
                json={"projectId": None},
                headers=csrf,
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/v1/conversation-messages", json=message(projectId=project["id"]), headers=csrf
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/v1/conversations/{turn['conversation']['id']}/jobs",
                json={"messageId": turn["messageId"], "jobId": str(uuid4())},
                headers=csrf,
            ).status_code
            == 404
        )


def task_fixture(api: dict[str, Any], turn: dict[str, Any]) -> UUID:
    """仅迁移身份构造DRAFT任务和ACTION解析夹具；不声称模型或执行链通过。"""
    job = uuid4()
    interpretation = {
        "kind": "ACTION",
        "text": "测试夹具动作",
        "action_type": "REQUEST_LIVE_START",
        "new_value": {"kind": "HANDOFF", "value": "START"},
        "target_type": "LIVE_SESSION",
        "candidate_ids": [str(uuid4())],
    }
    with psycopg.connect(api["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "INSERT INTO command_jobs(id,tenant_id,created_by,shop_id) VALUES(%s,%s,%s,%s)",
            [job, api["tenant"], api["user"], api["shop"]],
        )
        control.execute(
            "UPDATE conversation_messages SET interpretation=%s::jsonb WHERE id=%s",
            [json.dumps(interpretation), UUID(turn["messageId"])],
        )
    return job


def test_m2_02_binding_and_assignment_match_frozen_client_routes(login_api: dict[str, Any]) -> None:
    sign_in(login_api)
    client = login_api["client"]
    projects = [
        client.post("/v1/projects", json={"name": name}, headers=headers(login_api)).json()["data"]
        for name in ("A", "B")
    ]
    turn = sent(login_api, projectId=projects[0]["id"])
    job = task_fixture(login_api, turn)
    path = f"/v1/conversations/{turn['conversation']['id']}/jobs"
    attach = client.post(
        path, json={"messageId": turn["messageId"], "jobId": str(job)}, headers=headers(login_api)
    )
    assert attach.status_code == 200, attach.text
    assert attach.json()["data"]["messages"][-1]["jobId"] == str(job)
    assignment = client.put(
        f"/v1/command-jobs/{job}/project",
        json={"projectId": projects[1]["id"]},
        headers=headers(login_api),
    )
    assert assignment.status_code == 200 and assignment.json()["data"] is None
    assert client.get("/v1/workspace/history").json()["data"]["taskProjects"] == {
        str(job): projects[1]["id"]
    }
    moved = client.patch(
        f"/v1/conversations/{turn['conversation']['id']}",
        json={"projectId": projects[1]["id"]},
        headers=headers(login_api),
    )
    assert moved.status_code == 200 and moved.json()["data"]["projectId"] == projects[1]["id"]
    assert client.get("/v1/workspace/history").json()["data"]["taskProjects"] == {}
    assert (
        client.put(
            f"/v1/command-jobs/{job}/project", json={"projectId": None}, headers=headers(login_api)
        ).status_code
        == 200
    )
    assert client.get("/v1/workspace/history").json()["data"]["taskProjects"] == {str(job): None}


def test_m2_02_cross_owner_task_and_unrecognized_message_rejected(
    login_api: dict[str, Any],
) -> None:
    sign_in(login_api)
    turn = sent(login_api)
    client = login_api["client"]
    assert (
        client.post(
            f"/v1/conversations/{turn['conversation']['id']}/jobs",
            json={"messageId": turn["messageId"], "jobId": str(uuid4())},
            headers=headers(login_api),
        ).status_code
        == 404
    )
    job = task_fixture(login_api, turn)
    with other_actor(login_api) as other:
        assert (
            other["client"]
            .put(
                f"/v1/command-jobs/{job}/project", json={"projectId": None}, headers=headers(other)
            )
            .status_code
            == 404
        )
        own = sent(other)
        task_fixture(other, own)
        assert (
            other["client"]
            .post(
                f"/v1/conversations/{own['conversation']['id']}/jobs",
                json={"messageId": own["messageId"], "jobId": str(job)},
                headers=headers(other),
            )
            .status_code
            == 404
        )


def test_m2_02_csrf_origin_privilege_and_private_actor_cleanup(login_api: dict[str, Any]) -> None:
    sign_in(login_api)
    client = login_api["client"]
    assert client.post("/v1/projects", json={"name": "无CSRF"}).status_code == 403
    assert (
        client.post(
            "/v1/projects",
            json={"name": "错误来源"},
            headers=headers(login_api) | {"Origin": "https://evil.invalid"},
        ).status_code
        == 403
    )
    sent(login_api)
    engine = create_engine(
        login_api["database"]["app"], pool_size=1, max_overflow=0, hide_parameters=True
    )
    try:
        identity = VerifiedIdentity(login_api["tenant"], login_api["member"], login_api["user"])
        with business_transaction(engine, identity) as session:
            assert session.execute(text("SELECT count(*) FROM conversations")).scalar_one() == 1
        with engine.connect() as raw:
            assert raw.execute(text("SELECT public.current_actor_id()")).scalar_one() is None
            assert raw.execute(text("SELECT count(*) FROM conversations")).scalar_one() == 0
        with engine.begin() as raw:
            raw.execute(
                text(
                    "SELECT set_config('app.current_tenant',:tenant,true),"
                    "set_config('app.current_actor','invalid',true)"
                ),
                {"tenant": str(login_api["tenant"])},
            )
            assert raw.execute(text("SELECT count(*) FROM conversations")).scalar_one() == 0
    finally:
        engine.dispose()
    with psycopg.connect(login_api["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute("UPDATE users SET status='DISABLED' WHERE id=%s", [login_api["user"]])
    assert client.get("/v1/workspace/history").status_code == 401


def test_m2_02_private_rls_also_denies_same_tenant_other_user(login_api: dict[str, Any]) -> None:
    sign_in(login_api)
    sent(login_api)
    engine = login_api["app"].state.identity_service.app
    with other_actor(login_api) as other:
        identity = VerifiedIdentity(other["tenant"], other["member"], other["user"])
        with business_transaction(engine, identity) as session:
            assert session.execute(text("SELECT count(*) FROM conversations")).scalar_one() == 0
            assert (
                session.execute(text("SELECT count(*) FROM conversation_messages")).scalar_one()
                == 0
            )
            assert (
                session.execute(text("SELECT count(*) FROM conversation_turns")).scalar_one() == 0
            )
        with pytest.raises(DBAPIError), business_transaction(engine, identity) as session:
            session.execute(
                text(
                    "INSERT INTO work_projects(tenant_id,user_id,name) "
                    "VALUES(:tenant,:user,'Forged')"
                ),
                {"tenant": login_api["tenant"], "user": login_api["user"]},
            )


def test_m2_02_scope_revocation_denies_binding_without_mutating_job(
    login_api: dict[str, Any],
) -> None:
    sign_in(login_api)
    turn = sent(login_api)
    job = task_fixture(login_api, turn)
    with psycopg.connect(login_api["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "DELETE FROM data_scopes WHERE tenant_id=%s AND membership_id=%s",
            [login_api["tenant"], login_api["member"]],
        )
    client = login_api["client"]
    response = client.put(
        f"/v1/command-jobs/{job}/project", json={"projectId": None}, headers=headers(login_api)
    )
    assert response.status_code == 403
    with psycopg.connect(login_api["database"]["admin"]) as control:
        assert control.execute("SELECT status FROM command_jobs WHERE id=%s", [job]).fetchone() == (
            "DRAFT",
        )
        assert control.execute(
            "SELECT count(*) FROM task_project_links WHERE job_id=%s", [job]
        ).fetchone() == (0,)


def test_m2_02_permission_revocation_rechecked_on_next_request(login_api: dict[str, Any]) -> None:
    sign_in(login_api)
    sent(login_api)
    with psycopg.connect(login_api["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "DELETE FROM role_grants WHERE tenant_id=%s AND membership_id=%s",
            [login_api["tenant"], login_api["member"]],
        )
    assert login_api["client"].get("/v1/workspace/history").status_code == 403
