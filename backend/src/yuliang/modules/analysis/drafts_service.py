"""草稿CAS、版本历史及固定转义预览；冻结分析证据不可编辑。"""

from html import escape
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.analysis.drafts_models import ReportDraft
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class DraftConflict(ApiError):
    def __init__(self, current: dict[str, Any] | None):
        super().__init__(ErrorCode.VERSION_CONFLICT, "草稿已更新，请保留编辑副本并核对当前版本")
        self.current = current


class DraftService:
    def __init__(self, engine: Engine, identity: VerifiedIdentity):
        self.engine, self.identity = engine, identity

    def get(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as s:
            value = s.execute(
                text("SELECT payload FROM report_drafts WHERE id=:id"), dict(id=identifier)
            ).scalar_one_or_none()
            if value is None:
                raise ApiError(ErrorCode.NO_DATA, "报告草稿不存在或当前范围不可见")
            return dict(value)

    def list(self) -> list[dict[str, Any]]:
        with business_transaction(self.engine, self.identity) as s:
            rows = (
                s.execute(text("SELECT payload FROM report_drafts ORDER BY id LIMIT 1001"))
                .scalars()
                .all()
            )
            if len(rows) > 1000:
                raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "请收窄草稿查询范围")
            return [dict(r) for r in rows]

    def save(self, identifier: UUID, value: ReportDraft) -> dict[str, Any]:
        if identifier != value.id:
            raise ApiError(ErrorCode.INVALID_INPUT, "草稿ID与路径不符")
        try:
            with business_transaction(self.engine, self.identity) as s:
                result = s.execute(
                    text(
                        "SELECT access.report_draft_save(:id,:analysis,:version,"
                        "CAST(:payload AS jsonb))"
                    ),
                    dict(
                        id=identifier,
                        analysis=value.analysis_id,
                        version=value.version,
                        payload=value.model_dump_json(by_alias=True, exclude_none=True),
                    ),
                ).scalar_one()
                return dict(result)
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None)
            if code == "P0409":
                try:
                    current = self.get(identifier)
                except ApiError:
                    current = None
                raise DraftConflict(current) from None
            if code == "42501":
                raise ApiError(ErrorCode.FORBIDDEN, "无当前报告来源或草稿范围") from None
            if code in {"22023", "22P02"}:
                raise ApiError(
                    ErrorCode.INVALID_INPUT, "草稿证据、身份或周期不符合冻结来源"
                ) from None
            raise

    def preview(self, identifier: UUID) -> str:
        return preview_html(self.get(identifier))


def preview_html(draft: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return escape(str(value), quote=True)

    metrics = {m["id"]: m for m in draft["metrics"]}
    sections = []
    for module in draft["modules"]:
        if not module["visible"]:
            continue
        cards = ""
        if module.get("showMetrics", True):
            cards = "".join(
                "<p>"
                + esc(metrics[k]["name"])
                + "："
                + esc(metrics[k]["value"] if metrics[k]["value"] is not None else "暂无可计算数据")
                + "</p>"
                for k in module["metricIds"]
            )
        sections.append(
            "<section><h2>"
            + esc(module["title"])
            + "</h2>"
            + cards
            + '<p class="body">'
            + esc(module["text"])
            + "</p></section>"
        )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src '
        "'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\"><title>"
        + esc(draft["title"])
        + "</title><style>.body{white-space:pre-wrap;overflow-wrap:anywhere}"
        "body{font-family:system-ui;margin:32px}section{margin:24px 0}"
        "</style></head><body><h1>"
        + esc(draft["title"])
        + "</h1><p>"
        + esc(draft["query"]["startDate"])
        + " — "
        + esc(draft["query"]["endDate"])
        + " Asia/Shanghai</p><p>"
        + esc(draft["coverage"])
        + "</p>"
        + "".join(sections)
        + "<footer>冻结分析来源 "
        + esc(draft["analysisId"])
        + "；草稿版本 "
        + esc(draft["version"])
        + "</footer></body></html>"
    )
