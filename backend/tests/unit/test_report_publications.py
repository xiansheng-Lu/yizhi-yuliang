from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from yuliang.main import create_app
from yuliang.modules.analysis.publications import link_token, public_html, token_digest
from yuliang.platform.security.public_report import PublicReportPrivacy
from yuliang.settings import Settings


def test_token_bound_to_id_nonce_key_and_no_raw_digest() -> None:
    identifier = uuid4()
    token = link_token(b"independent-test-key", identifier, "a" * 64)
    assert len(token) == 43 and len(token_digest(token)) == 64
    assert link_token(b"independent-test-key", identifier, "a" * 64) == token
    assert link_token(b"independent-test-key", uuid4(), "a" * 64) != token
    assert link_token(b"independent-test-key", identifier, "b" * 64) != token
    assert link_token(b"changed-key", identifier, "a" * 64) != token


@pytest.mark.parametrize(
    "query", ["token=abc", "token=abc&token=def", "other=secret", "token=" + "a" * 500]
)
def test_privacy_removes_query_before_inner_app(query: str) -> None:
    from starlette.responses import JSONResponse

    async def probe(scope: dict[str, Any], receive: Any, send: Any) -> None:
        assert scope["query_string"] == b""
        await JSONResponse(dict(redacted=True))(scope, receive, send)

    client = TestClient(PublicReportPrivacy(probe))
    assert client.get("/reports/public/id?" + query).json() == dict(redacted=True)


def test_public_html_escapes_and_hides_unselected_data() -> None:
    draft = dict(
        title="<script>attack()</script>",
        query=dict(startDate="2026-10-01", endDate="2026-10-07", shopId="internal-shop"),
        analysisId="internal-analysis",
        version=3,
        coverage="covered",
        attribution="gross",
        metrics=[dict(id="ads.gmv", name="<img onerror=x>", value=10)],
        points=[dict(date="2026-10-01", values={"ads.gmv": 10})],
        modules=[
            dict(
                title="Visible",
                text="<img src=x onerror=x>",
                visible=True,
                showMetrics=True,
                showChart=True,
                metricIds=["ads.gmv"],
            ),
            dict(title="HIDDEN_TITLE", text="HIDDEN_TEXT", visible=False),
        ],
    )
    html = public_html(draft)
    assert "<script>" not in html and "<img " not in html and "&lt;script&gt;" in html
    assert "internal-shop" not in html and "internal-analysis" not in html
    assert "HIDDEN_" not in html and "2026-10-01" in html and "<td>10</td>" in html
    assert "no-referrer" in html and "Content-Security-Policy" in html


def test_publication_configuration_is_explicit_and_secret_independent(settings: Settings) -> None:
    values = settings.model_dump() | dict(
        identity_api_enabled=True,
        auth_audit_secret="a" * 32,
        allowed_origins=["http://localhost:3000"],
        report_link_secret="b" * 32,
    )
    for origin in (
        None,
        "https://example.invalid/path",
        "https://user:secret@example.invalid",
        "http://example.invalid",
    ):
        with pytest.raises(ValidationError):
            Settings(**(values | dict(report_public_origin=origin)))
    valid = Settings(**(values | dict(report_public_origin="https://reports.example.invalid")))
    assert "b" * 32 not in repr(valid)
    with pytest.raises(ValidationError):
        Settings(
            **(
                values
                | dict(
                    report_public_origin="https://reports.example.invalid",
                    report_link_secret=settings.session_secret,
                )
            )
        )
    assert create_app(settings).state.settings.report_public_origin is None
