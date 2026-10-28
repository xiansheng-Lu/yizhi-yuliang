"""真实分析结果→报告草稿/CAS/不可变来源/安全预览。"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from yuliang.modules.analysis.drafts_models import ReportDraft
from yuliang.modules.analysis.drafts_service import DraftConflict, DraftService
from yuliang.modules.analysis.repository import execute_analysis
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_analysis_admission import (
    analysis_ready as analysis_ready,
)
from .test_analysis_admission import (
    capability as capability,
)
from .test_analysis_admission import (
    configuration_ready as configuration_ready,
)
from .test_analysis_admission import (
    database as database,
)
from .test_analysis_admission import (
    governance as governance,
)
from .test_analysis_admission import (
    legacy as legacy,
)
from .test_analysis_admission import (
    login_api as login_api,
)
from .test_analysis_admission import (
    oauth_ready as oauth_ready,
)
from .test_analysis_admission import (
    reports as reports,
)
from .test_password_sessions import challenge

pytestmark = pytest.mark.integration


@pytest.fixture
def draft_ready(analysis_ready: dict[str, Any]) -> dict[str, Any]:
    d = analysis_ready
    task = d["analysis"].create(d["input"])
    assert execute_analysis(d["engine"], d["identity"], task.id)
    done = d["analysis"].get(task.id)
    result = d["analysis"].results(done.result_id)[0]
    payload = {
        k: result[k]
        for k in (
            "query",
            "shopName",
            "platformName",
            "metrics",
            "points",
            "sections",
            "insights",
            "coverage",
            "attribution",
        )
    }
    payload.update(
        schemaVersion="business-report-v1",
        id=str(uuid4()),
        analysisId=result["id"],
        title="Synthetic review",
        period="weekly",
        source="API",
        updatedAt=result["createdAt"],
        version=0,
        modules=[
            dict(
                id="summary",
                title="Summary",
                text="manual",
                visible=True,
                showChart=False,
                metricIds=[result["metrics"][0]["id"]],
            )
        ],
    )
    return d | dict(draft=payload, drafts=DraftService(d["engine"], d["identity"]))


def test_draft_http_save_conflict_preview_escaping(draft_ready: dict[str, Any]) -> None:
    d = draft_ready
    payload = d["draft"]
    client = d["client"]
    path = "/v1/business-analysis/drafts/" + payload["id"]
    headers = {"X-CSRF-Token": challenge(client)}
    payload["title"] = "<script>private()</script>"
    payload["modules"][0]["text"] = "<img src=x onerror=private()>"
    response = client.put(path, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    saved = response.json()["data"]
    assert saved["version"] == 1
    assert isinstance(client.get("/v1/business-analysis/drafts").json()["data"], list)
    conflict = client.put(path, json=payload, headers=headers)
    assert conflict.status_code == 409 and conflict.json()["error"]["current"] == saved
    preview = client.get(path + "/preview")
    assert (
        preview.status_code == 200
        and "<script>" not in preview.text
        and "<img " not in preview.text
    )
    assert (
        "&lt;script&gt;" in preview.text
        and "default-src 'none'" in preview.headers["Content-Security-Policy"]
    )
    assert d["analysis"].results()[0]["id"] == payload["analysisId"]


def test_two_writers_one_wins_and_history_preserved(draft_ready: dict[str, Any]) -> None:
    d = draft_ready
    first = d["drafts"].save(
        ReportDraft.model_validate(d["draft"]).id, ReportDraft.model_validate(d["draft"])
    )

    def save(title: str) -> str:
        value = ReportDraft.model_validate(first | dict(title=title))
        try:
            d["drafts"].save(value.id, value)
            return "saved"
        except DraftConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(save, ["left", "right"]))
    assert sorted(states) == ["conflict", "saved"]
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM report_draft_versions")).scalar_one() == 2


def test_forged_evidence_and_scope_cannot_overwrite(draft_ready: dict[str, Any]) -> None:
    d = draft_ready
    value = ReportDraft.model_validate(d["draft"])
    original = d["drafts"].save(value.id, value)
    forged = ReportDraft.model_validate(original | dict(coverage="FORGED"))
    with pytest.raises(ApiError):
        d["drafts"].save(value.id, forged)
    assert d["drafts"].get(value.id) == original
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:id"),
            dict(id=d["identity"].membership_id),
        )
    with pytest.raises(ApiError):
        d["drafts"].get(value.id)


def test_direct_app_role_missing_or_null_identity_fields_rejected(
    draft_ready: dict[str, Any],
) -> None:
    import json

    from sqlalchemy.exc import DBAPIError

    d = draft_ready
    original = d["draft"]
    for field in ("schemaVersion", "source", "id", "analysisId", "version"):
        for mode in ("missing", "null"):
            value = dict(original)
            if mode == "missing":
                value.pop(field)
            else:
                value[field] = None
            with business_transaction(d["engine"], d["identity"]) as session:
                assert session.execute(text("SELECT current_user")).scalar_one() == "app_login"
                with pytest.raises(DBAPIError) as error:
                    session.execute(
                        text(
                            "SELECT access.report_draft_save(:id,:analysis,0,"
                            "CAST(:payload AS jsonb))"
                        ),
                        dict(
                            id=original["id"],
                            analysis=original["analysisId"],
                            payload=json.dumps(value),
                        ),
                    )
                assert getattr(error.value.orig, "sqlstate", None) == "22023"
    assert not d["drafts"].list()
    with business_transaction(d["engine"], d["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM report_draft_versions")).scalar_one() == 0
