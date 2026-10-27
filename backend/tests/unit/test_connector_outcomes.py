"""200不是业务成功，写未知不转失败，未知敏感字段不进入存档投影。"""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from uuid import uuid4

import pytest
from pydantic import SecretStr

from yuliang.modules.connectors.http import ConnectorTransportError, HttpExchange
from yuliang.modules.connectors.outcomes import BusinessVerdict, classify
from yuliang.modules.connectors.redaction import ArchivePolicy, canonical, fingerprint, project


def response(status: int, headers: dict[str, str] | None = None) -> HttpExchange:
    return HttpExchange(uuid4(), "synthetic", status, 1, datetime.now(UTC), headers or {}, b"{}")


def test_200_needs_business_confirmation_and_acceptance_is_not_matching() -> None:
    assert classify("WRITE", response=response(200)).external == "UNKNOWN"
    assert classify("READ", response=response(200)).external == "READ_FAILED"
    confirmed = BusinessVerdict("CONFIRMED_SUCCESS")
    assert classify("WRITE", response=response(200), verdict=confirmed).external == "ACCEPTED"
    assert classify("READ", response=response(200), verdict=confirmed).external == "READ_SUCCEEDED"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_write_server_errors_stay_unknown_even_with_untrusted_rejection_body(status: int) -> None:
    value = classify(
        "WRITE",
        response=response(status),
        verdict=BusinessVerdict("CONFIRMED_REJECTION", "PARAMETER"),
    )
    assert (
        value.category == "UPSTREAM_ERROR"
        and value.external == "UNKNOWN"
        and not value.retryable_read
    )


@pytest.mark.parametrize("status", [200, 400, 422])
def test_explicit_parameter_rejection_is_separate_from_unconfirmed_http_error(status: int) -> None:
    value = classify(
        "WRITE",
        response=response(status),
        verdict=BusinessVerdict("CONFIRMED_REJECTION", "PARAMETER"),
    )
    assert value.category == "PARAMETER_REJECTED" and value.external == "REJECTED"


def test_rate_limit_numeric_date_and_unconfirmed_wait_are_preserved() -> None:
    value = classify("WRITE", response=response(429, {"retry-after": "12"}))
    assert (
        value.category == "RATE_LIMITED"
        and value.retry_after_seconds == 12
        and not value.retryable_read
    )
    date = format_datetime(datetime.now(UTC) + timedelta(seconds=60), usegmt=True)
    value = classify("READ", response=response(429, {"retry-after": date}))
    assert value.retry_after_seconds is not None and 50 <= value.retry_after_seconds <= 60
    for bad in ["secret-not-a-date", "NaN", "10000000000"]:
        assert classify(
            "READ", response=response(429, {"retry-after": bad})
        ).retry_after_unconfirmed


def test_transport_loss_never_echoes_raw_error_or_marks_write_failed() -> None:
    for code in ["TIMEOUT", "RESPONSE_TOO_LARGE", "untrusted-credential-text"]:
        result = classify("WRITE", error=ConnectorTransportError(code, uuid4(), 1))
        assert result.external == "UNKNOWN" and not result.retryable_read
        assert "untrusted-credential-text" not in repr(result)
    assert (
        classify(
            "WRITE", error=ConnectorTransportError("SIGNING_ERROR", uuid4(), 0, dispatch="NOT_SENT")
        ).external
        == "NOT_SENT"
    )


def test_explicit_policy_cannot_override_credentials_or_order_pii_redaction() -> None:
    policy = ArchivePolicy(
        ArchivePolicy().fields
        | frozenset(
            {
                "name",
                "receiver_name",
                "phone",
                "email",
                "access_token",
                "MysteryCredential",
                "shipping_address",
                "unknown_nested",
                "message",
            }
        )
    )
    value = {
        "code": "oauth-secret",
        "errcode": 0,
        "access_token": "private-token",
        "data": {
            "budget_minor": 123,
            "receiver_name": "Private Person",
            "phone": "13800000000",
            "email": "synthetic@example.invalid",
            "MysteryCredential": "private-secret",
            "shipping_address": "private-address",
            "unlisted": "private-other",
        },
    }
    safe = project(value, policy)
    assert safe["data"] == {"errcode": 0, "data": {"budget_minor": 123}}
    assert "private-" not in str(safe) and "13800000000" not in str(safe)
    assert safe["omittedFields"] >= 7


def test_projection_limits_and_keyed_domain_separation_are_explicit() -> None:
    safe = project({"rows": [{"id": 1}, {"id": 2}, {"id": 3}]}, ArchivePolicy(max_items=2))
    assert safe["truncated"] and len(safe["data"]["rows"]) == 2
    key = SecretStr("synthetic-audit-key-only-at-least-32-chars")
    raw = canonical({"b": 2, "a": 1})
    assert raw == canonical({"a": 1, "b": 2})
    assert fingerprint(key, "request", raw) != fingerprint(key, "response", raw)
    with pytest.raises(ValueError):
        fingerprint(SecretStr("short"), "request", raw)
    with pytest.raises(ValueError):
        canonical({"value": float("nan")})
