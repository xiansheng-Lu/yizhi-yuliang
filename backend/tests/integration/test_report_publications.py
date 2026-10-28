"""真实PG/Redis/HTTP发布、匿名读、版本幂等、吊销与过期。"""

import json
import os
import secrets
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.testclient import TestClient

from yuliang.modules.analysis.drafts_models import ReportDraft
from yuliang.modules.analysis.publications import PublicationCreate, PublicationService, read_public
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_password_sessions import challenge
from .test_report_drafts import (  # noqa: F401 -- pytest传递夹具
    analysis_ready,
    capability,
    configuration_ready,
    database,
    governance,
    legacy,
    login_api,
    oauth_ready,
    reports,
)
from .test_report_drafts import (
    draft_ready as draft_ready,
)

pytestmark = pytest.mark.integration
BASE = "/v1/business-analysis"


@pytest.fixture
def publication_ready(draft_ready: dict[str, Any]) -> dict[str, Any]:
    d = draft_ready
    value = ReportDraft.model_validate(d["draft"])
    saved = d["drafts"].save(value.id, value)
    settings = d["settings"].model_copy(
        update=dict(
            report_public_origin="http://testserver",
            report_link_secret=SecretStr(secrets.token_hex(32)),
        )
    )
    d["app"].state.settings = settings
    return d | dict(
        saved=saved,
        settings=settings,
        publications=PublicationService(d["engine"], d["identity"], settings),
    )


def create(d: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    return d["publications"].create(
        UUID(d["saved"]["id"]),
        PublicationCreate(version=d["saved"]["version"], idempotencyKey=key or str(uuid4())),
    )


def test_http_publish_anon_read_fixed_snapshot_and_export(
    publication_ready: dict[str, Any],
) -> None:
    d = publication_ready
    path = BASE + "/drafts/" + d["saved"]["id"] + "/publications"
    value = dict(version=1, idempotencyKey=str(uuid4()))
    headers = {"X-CSRF-Token": challenge(d["client"])}
    assert d["client"].post(path, json=value).status_code == 403
    assert (
        d["client"]
        .post(path, json=value | dict(publicOrigin="https://evil.invalid"), headers=headers)
        .status_code
        == 422
    )
    response = d["client"].post(path, json=value, headers=headers)
    assert response.status_code == 200, response.text
    first = response.json()["data"]
    assert d["client"].post(path, json=value, headers=headers).json()["data"] == first
    assert d["client"].get(BASE + "/publications/" + first["id"]).json()["data"] == first
    assert first in d["client"].get(BASE + "/publications").json()["data"]
    d["drafts"].save(
        UUID(d["saved"]["id"]), ReportDraft.model_validate(d["saved"] | dict(title="New title"))
    )
    assert d["publications"].get(UUID(first["id"]))["snapshot"] == d["saved"]
    exported = d["client"].get(BASE + "/publications/" + first["id"] + "/export")
    assert exported.status_code == 200 and "Synthetic review" in exported.text
    assert "attachment" in exported.headers["Content-Disposition"]
    assert "New title" not in exported.text
    assert d["client"].post(path, json=value, headers=headers).json()["data"] == first
    with TestClient(d["app"], raise_server_exceptions=False) as anonymous:
        assert not anonymous.cookies
        read = anonymous.get(first["url"])
        assert read.status_code == 200, read.text
        assert "Synthetic review" in read.text and "New title" not in read.text
        assert first["id"] not in read.text and d["saved"]["analysisId"] not in read.text
        assert "no-referrer" == read.headers["Referrer-Policy"]
        assert "default-src 'none'" in read.headers["Content-Security-Policy"]
        assert anonymous.get(BASE + "/publications/" + first["id"]).status_code == 401
        # 同一token不能用于其他报告ID，不能在匿名路径枚举业务目录。
        parts = urlsplit(first["url"])
        assert (
            anonymous.get("/reports/public/" + str(uuid4()) + "?" + parts.query).status_code == 404
        )
        denied = anonymous.get("/v1/frontend/snapshot?" + parts.query)
        assert denied.status_code in {401, 422} and "data" not in denied.json()


def test_concurrent_key_idempotency_version_mismatch_and_hash_only(
    publication_ready: dict[str, Any],
) -> None:
    d = publication_ready
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: create(d, key), range(2)))
    assert rows[0] == rows[1]
    with business_transaction(d["engine"], d["identity"]) as session:
        row = session.execute(
            text("SELECT * FROM report_publications WHERE id=:id"), dict(id=rows[0]["id"])
        ).first()
        assert row is not None and len(row.token_hash) == 64
        assert "token=" not in str(row) and urlsplit(rows[0]["url"]).query.split("=", 1)[
            1
        ] not in str(row)
    d["drafts"].save(
        UUID(d["saved"]["id"]), ReportDraft.model_validate(d["saved"] | dict(title="v2"))
    )
    with pytest.raises(ApiError) as conflict:
        create(d)
    assert conflict.value.code == ErrorCode.VERSION_CONFLICT
    with pytest.raises(ApiError) as mismatch:
        d["publications"].create(
            UUID(d["saved"]["id"]), PublicationCreate(version=2, idempotencyKey=key)
        )
    assert mismatch.value.code == ErrorCode.IDEMPOTENCY_MISMATCH
    second = d["publications"].create(
        UUID(d["saved"]["id"]), PublicationCreate(version=2, idempotencyKey=str(uuid4()))
    )
    assert second["id"] != rows[0]["id"] and second["snapshot"]["version"] == 2


def test_revoke_expire_and_rate_limit(publication_ready: dict[str, Any]) -> None:
    d = publication_ready
    first = create(d)
    assert d["client"].get(first["url"]).status_code == 200
    response = d["client"].post(
        BASE + "/publications/" + first["id"] + "/revoke",
        headers={"X-CSRF-Token": challenge(d["client"])},
    )
    assert response.status_code == 200
    d["publications"].revoke(UUID(first["id"]))  # 重复吊销不改变不可变版本。
    assert d["client"].get(first["url"]).status_code == 404
    d["publications"].ttl = 1
    expired = create(d)
    time.sleep(1.1)
    assert d["client"].get(expired["url"]).status_code == 404
    from yuliang.modules.analysis.publications import limit_public_read

    instance = d["app"].state.identity_service
    marker = uuid4().hex
    limit_public_read(instance.cache, marker, 1)
    with pytest.raises(ApiError) as limited:
        limit_public_read(instance.cache, marker, 1)
    assert limited.value.code == ErrorCode.RATE_LIMITED
    d["app"].state.settings = d["settings"].model_copy(
        update=dict(report_public_reads_per_minute=1)
    )
    limited_response = d["client"].get(first["url"])
    assert limited_response.status_code == 429 and limited_response.headers["Retry-After"] == "60"


def test_scope_and_db_direct_mutation_denied(publication_ready: dict[str, Any]) -> None:
    d = publication_ready
    first = create(d)
    for query in (
        "UPDATE report_publications SET snapshot='{}'::jsonb",
        "DELETE FROM report_publications",
    ):
        with business_transaction(d["engine"], d["identity"]) as session:
            with pytest.raises(DBAPIError):
                session.execute(text(query))
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        with pytest.raises(DBAPIError):
            owner.execute(
                text("UPDATE report_publications SET snapshot='{}'::jsonb WHERE id=:id"),
                dict(id=first["id"]),
            )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:id"),
            dict(id=d["identity"].membership_id),
        )
    assert d["publications"].list() == []
    with pytest.raises(ApiError):
        create(d)
    # 已明确发布的能力链接独立于会话，管理权限撤销不会自动撤回已交付链接；显式吊销或到期生效。
    assert read_public(
        d["engine"], UUID(first["id"]), urlsplit(first["url"]).query.split("=", 1)[1]
    )


def test_disabled_origin_does_not_return_fake_link(draft_ready: dict[str, Any]) -> None:
    d = draft_ready
    with pytest.raises(ApiError) as missing:
        PublicationService(d["engine"], d["identity"], d["settings"])
    assert missing.value.code == ErrorCode.CAPABILITY_UNAVAILABLE


def test_real_loopback_http_and_explicit_origin(publication_ready: dict[str, Any]) -> None:
    d = publication_ready
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    settings = d["settings"].model_copy(
        update=dict(report_public_origin=f"http://127.0.0.1:{port}")
    )
    d["app"].state.settings = settings
    publisher = PublicationService(d["engine"], d["identity"], settings)
    published = publisher.create(
        UUID(d["saved"]["id"]), PublicationCreate(version=1, idempotencyKey=str(uuid4()))
    )
    server = uvicorn.Server(
        uvicorn.Config(
            d["app"], lifespan="off", log_level="error", access_log=False, proxy_headers=False
        )
    )
    thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=3) as fresh_browser:
            response = fresh_browser.get(published["url"])
            assert response.status_code == 200 and "Synthetic review" in response.text
            assert not fresh_browser.cookies
            assert response.headers["cache-control"] == "no-store"
        if destination := os.getenv("YULIANG_PUBLICATION_CAPTURE"):
            Path(destination).write_text(
                json.dumps(
                    dict(
                        publication=published,
                        list=publisher.list(),
                        actualLoopbackHTTP=True,
                        anonymousStatus=response.status_code,
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
    assert not thread.is_alive()
