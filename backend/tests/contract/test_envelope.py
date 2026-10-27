import json
from typing import Annotated

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode


def test_success_200_202_and_null_keep_outer_version(app: FastAPI, client: TestClient) -> None:
    @app.post("/contract/accepted")
    async def accepted(request: Request) -> object:
        return success(request, {"contractVersion": "advertising-v9.0"}, status_code=202)

    @app.post("/contract/logout")
    async def logout(request: Request) -> object:
        return success(request, None)

    for url, method, expected in (
        ("/healthz", "GET", 200),
        ("/contract/accepted", "POST", 202),
        ("/contract/logout", "POST", 200),
    ):
        response = client.request(method, url)
        assert response.status_code == expected
        payload = response.json()
        assert payload["schemaVersion"] == "command-job-v1.0"
        assert payload["requestId"] == response.headers["X-Request-Id"]
        assert len(payload["requestId"]) == 32
        assert "data" in payload
    assert client.post("/contract/logout").json()["data"] is None
    assert client.post("/contract/accepted").json()["data"]["contractVersion"] == "advertising-v9.0"


@pytest.mark.parametrize(
    "code,status",
    [
        (ErrorCode.VERSION_CONFLICT, 409),
        (ErrorCode.LOCKED, 423),
        (ErrorCode.RISK_BLOCKED, 422),
        (ErrorCode.RATE_LIMITED, 429),
        (ErrorCode.RESULT_UNKNOWN, 504),
    ],
)
def test_controlled_error_status_details_and_no_write_replay(
    app: FastAPI,
    client: TestClient,
    code: ErrorCode,
    status: int,
) -> None:
    @app.post("/contract/failure")
    async def failure_route() -> None:
        raise ApiError(
            code, "受控错误", details=[{"field": "version", "reason": "已变化"}], retry_after=45
        )

    response = client.post("/contract/failure")
    error = response.json()["error"]
    assert response.status_code == status
    assert error["code"] == code
    assert error["details"] == [{"field": "version", "reason": "已变化"}]
    assert error["retryable"] is False
    if status == 429:
        assert response.headers["Retry-After"] == "45"


def test_validation_does_not_reflect_sensitive_input(app: FastAPI, client: TestClient) -> None:
    class Amount(BaseModel):
        amount_minor: Annotated[int, Field(strict=True, ge=0)]

    @app.post("/contract/amount")
    async def amount_route(value: Amount) -> object:
        return value

    response = client.post("/contract/amount", json={"amount_minor": "super-secret-password"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert "super-secret" not in response.text
    assert "input" not in response.text


def test_exception_request_id_is_correlated_without_secret_or_traceback(
    app: FastAPI,
    client: TestClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @app.get("/contract/crash")
    async def crash() -> None:
        raise RuntimeError("postgresql://admin:do-not-leak@example/db")

    response = client.get("/contract/crash", headers={"X-Request-Id": "forged-client-id"})
    request_id = response.json()["requestId"]
    captured = capsys.readouterr()
    assert response.status_code == 500
    assert request_id == response.headers["X-Request-Id"] and request_id != "forged-client-id"
    assert "do-not-leak" not in response.text + captured.out + captured.err
    assert "Traceback" not in response.text
    logs = [json.loads(line) for line in captured.out.splitlines() if line.startswith("{")]
    assert any(
        row.get("event") == "request_failed" and row["request_id"] == request_id for row in logs
    )


def test_missing_route_has_error_envelope(client: TestClient) -> None:
    response = client.get("/not-an-api")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_DATA"


def test_read_throttling_exposes_retry_after(app: FastAPI, client: TestClient) -> None:
    @app.get("/contract/throttle")
    async def throttle() -> None:
        raise ApiError(ErrorCode.RATE_LIMITED, "请稍后再读", retry_after=2)

    response = client.get("/contract/throttle")
    assert response.status_code == 429
    assert response.json()["error"]["retryable"] is True
    assert response.headers["Retry-After"] == "2"
