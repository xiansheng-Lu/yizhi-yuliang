"""真实依赖与本地拒绝端口，验证运维状态、来源与MQ/Redis降级。"""

from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.infrastructure.outbox import celery_sender, dispatch_pending, emit
from yuliang.main import create_app
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.messages import WorkMessage
from yuliang.settings import Settings
from yuliang.worker.app import create_celery

from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api
from .test_password_sessions import sign_in


def disconnected(value: SecretStr) -> SecretStr:
    parsed = urlsplit(value.get_secret_value())
    return SecretStr(
        urlunsplit((parsed.scheme, parsed.netloc.rsplit(":", 1)[0] + ":1", parsed.path, "", ""))
    )


def operational(settings: Settings) -> TestClient:
    return TestClient(
        create_app(settings), client=("127.0.0.1", 50002), raise_server_exceptions=False
    )


def test_m0_03_real_healthy_and_metrics_are_low_cardinality(login_api: dict[str, Any]) -> None:
    with operational(login_api["settings"]) as client:
        response = client.get("/readyz")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["ready"] and not data["degraded"] and data["dispatchTransportReady"]
        assert set(data["dependencies"].values()) == {"UP"}
        assert data["workerState"] == "UNOBSERVED" and data["businessExecutionEnabled"] is False
        metrics = client.get("/metrics")
        assert metrics.status_code == 200 and "text/plain" in metrics.headers["content-type"]
        assert 'yuliang_dependency_up{dependency="postgres_app"} 1' in metrics.text
        assert 'yuliang_dependency_up{dependency="rabbitmq"} 1' in metrics.text
        assert "yuliang_dispatch_transport_ready 1" in metrics.text
        for value in (
            login_api["email"],
            login_api["password"],
            str(login_api["tenant"]),
            str(login_api["user"]),
        ):
            if len(value) > 5:
                assert value not in metrics.text
        for field in (
            "database_url",
            "auth_database_url",
            "redis_url",
            "broker_url",
            "session_secret",
            "auth_audit_secret",
        ):
            assert (
                getattr(login_api["settings"], field).get_secret_value()
                not in response.text + metrics.text
            )
        assert "inflight" not in metrics.text and "tenant_id" not in metrics.text


@pytest.mark.parametrize("field", ["database_url", "auth_database_url"])
def test_m0_03_database_down_is_not_ready_but_alive(login_api: dict[str, Any], field: str) -> None:
    settings = login_api["settings"].model_copy(
        update={field: disconnected(getattr(login_api["settings"], field))}
    )
    with operational(settings) as client:
        assert client.get("/healthz").status_code == 200
        response = client.get("/readyz")
        assert response.status_code == 503 and response.json()["data"]["ready"] is False
        assert "Connection refused" not in response.text
        assert getattr(settings, field).get_secret_value() not in response.text


def test_m0_03_mq_down_preserves_reads_and_pending_requests(login_api: dict[str, Any]) -> None:
    sign_in(login_api)
    settings = login_api["settings"].model_copy(
        update={"broker_url": disconnected(login_api["settings"].broker_url)}
    )
    identity = VerifiedIdentity(login_api["tenant"], login_api["member"], login_api["user"])
    engine = login_api["app"].state.identity_service.app
    items = [
        WorkMessage(
            event_id=uuid4(),
            job_id=uuid4(),
            tenant_id=identity.tenant_id,
            membership_id=identity.membership_id,
            user_id=identity.user_id,
        )
        for _ in range(2)
    ]
    with business_transaction(engine, identity) as session:
        for item in items:
            emit(session, item, "yuliang.system.schedule_recovery")
    broker = create_celery(settings)
    try:
        assert dispatch_pending(engine, identity, celery_sender(broker)) == 0
    finally:
        broker.close()
    with business_transaction(engine, identity) as session:
        rows = session.execute(text("SELECT state,attempts FROM outbox_events ORDER BY id")).all()
        assert [tuple(row) for row in rows] == [("PENDING", 1), ("PENDING", 0)]
    with operational(settings) as client:
        client.cookies.update(login_api["client"].cookies)
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["data"]["dispatchTransportReady"] is False
        assert ready.json()["data"]["dependencies"]["rabbitmq"] == "DOWN"
        assert client.get("/v1/frontend/snapshot").status_code == 200
        assert 'yuliang_dependency_up{dependency="rabbitmq"} 0' in client.get("/metrics").text


def test_m0_03_redis_down_keeps_postgres_session_authority(login_api: dict[str, Any]) -> None:
    sign_in(login_api)
    settings = login_api["settings"].model_copy(
        update={"redis_url": disconnected(login_api["settings"].redis_url)}
    )
    with operational(settings) as client:
        client.cookies.update(login_api["client"].cookies)
        ready = client.get("/readyz")
        assert ready.status_code == 200 and ready.json()["data"]["degraded"]
        assert ready.json()["data"]["cacheFallback"] == "POSTGRESQL"
        assert ready.json()["data"]["dependencies"]["redis"] == "DOWN"
        session = client.get("/v1/auth/session")
        assert session.status_code == 200 and session.json()["data"]["displayName"] == "User"


@pytest.mark.parametrize("host", ["203.0.113.77", "unknown-client"])
def test_m0_03_forged_forwarding_headers_cannot_read_metrics(settings: Settings, host: str) -> None:
    with TestClient(
        create_app(settings), client=(host, 50003), raise_server_exceptions=False
    ) as client:
        for path in ("/readyz", "/metrics"):
            response = client.get(
                path, headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"}
            )
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "FORBIDDEN"
