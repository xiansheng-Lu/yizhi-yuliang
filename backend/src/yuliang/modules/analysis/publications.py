"""固定草稿版本发布、能力链接摘要与独立免登录读取。"""

import base64
import hashlib
import hmac
import secrets
import time
from html import escape
from typing import Any
from uuid import UUID, uuid4

import redis
from pydantic import Field
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.analysis.models import AnalysisModel
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode
from yuliang.settings import Settings


class PublicationCreate(AnalysisModel):
    version: int = Field(strict=True, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200, pattern=r"^\S+$")


def link_token(secret: bytes, identifier: UUID, nonce: str) -> str:
    value = hmac.new(
        secret, f"report-publication-v1:{identifier}:{nonce}".encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class PublicationService:
    def __init__(self, engine: Engine, identity: VerifiedIdentity, settings: Settings):
        if settings.report_public_origin is None or settings.report_link_secret is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "报告公开源及独立链接密钥尚未配置")
        self.engine, self.identity = engine, identity
        self.origin = settings.report_public_origin
        self.key = settings.report_link_secret.get_secret_value().encode()
        self.ttl = settings.report_public_ttl_seconds

    def _project(self, row: Any) -> dict[str, Any]:
        token = link_token(self.key, row.id, row.nonce)
        if not hmac.compare_digest(token_digest(token), row.token_hash):
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "原发布链接密钥不可用，请恢复既有密钥")
        draft = dict(row.snapshot)
        return dict(
            id=str(row.id),
            draftId=str(row.draft_id),
            source="API",
            url=f"{row.public_origin}/reports/public/{row.id}?token={token}",
            publishedAt=row.published_at.isoformat(),
            expiresAt=row.expires_at.isoformat(),
            revokedAt=row.revoked_at.isoformat() if row.revoked_at else None,
            snapshot=draft,
            title=draft["title"],
            period=draft["period"],
            query=draft["query"],
            analysisId=draft["analysisId"],
        )

    def get(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as s:
            row = s.execute(
                text("SELECT * FROM report_publications WHERE id=:id"), dict(id=identifier)
            ).first()
            if row is None:
                raise ApiError(ErrorCode.NO_DATA, "发布版本不存在或当前范围不可见")
            return self._project(row)

    def list(self) -> list[dict[str, Any]]:
        with business_transaction(self.engine, self.identity) as s:
            rows = s.execute(
                text("SELECT * FROM report_publications ORDER BY published_at DESC,id LIMIT 1001")
            ).all()
            if len(rows) > 1000:
                raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "发布历史过大，请收窄范围")
            return [self._project(row) for row in rows]

    def create(self, draft_id: UUID, value: PublicationCreate) -> dict[str, Any]:
        identifier, nonce = uuid4(), secrets.token_hex(32)
        token = link_token(self.key, identifier, nonce)
        try:
            with business_transaction(self.engine, self.identity) as s:
                identifier = s.execute(
                    text(
                        "SELECT access.report_publish(:id,:draft,:version,:key,"
                        ":hash,:nonce,:origin,:ttl)"
                    ),
                    dict(
                        id=identifier,
                        draft=draft_id,
                        version=value.version,
                        key=value.idempotency_key,
                        hash=token_digest(token),
                        nonce=nonce,
                        origin=self.origin,
                        ttl=self.ttl,
                    ),
                ).scalar_one()
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None)
            if code == "P0410":
                raise ApiError(
                    ErrorCode.IDEMPOTENCY_MISMATCH, "同发布键已绑定其他草稿版本"
                ) from None
            if code == "P0409":
                raise ApiError(
                    ErrorCode.VERSION_CONFLICT, "草稿版本已变化，请先保存并核对当前版本"
                ) from None
            if code == "42501":
                raise ApiError(ErrorCode.FORBIDDEN, "无当前报告发布范围") from None
            if code in {"22023", "22P02"}:
                raise ApiError(ErrorCode.INVALID_INPUT, "发布参数无效") from None
            raise
        return self.get(identifier)

    def revoke(self, identifier: UUID) -> None:
        try:
            with business_transaction(self.engine, self.identity) as s:
                found = s.execute(
                    text("SELECT access.report_revoke(:id)"), dict(id=identifier)
                ).scalar_one()
                if not found:
                    raise ApiError(ErrorCode.NO_DATA, "发布版本不存在或当前范围不可见")
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "42501":
                raise ApiError(ErrorCode.FORBIDDEN, "无当前报告吊销范围") from None
            raise


def limit_public_read(cache: redis.Redis, ip_digest: str, limit: int) -> None:
    key = f"report-public-read:{ip_digest}:{int(time.time()) // 60}"
    try:
        count = cache.eval(
            "local n=redis.call('INCR',KEYS[1]); if n==1 then "
            "redis.call('EXPIRE',KEYS[1],70) end; return n",
            1,
            key,
        )
    except redis.RedisError:
        raise ApiError(ErrorCode.UPSTREAM_ERROR, "公开报告读取保护暂不可用") from None
    if int(count) > limit:  # type: ignore[arg-type]
        raise ApiError(ErrorCode.RATE_LIMITED, "公开报告访问过于频繁，请稍后再试", retry_after=60)


def read_public(engine: Engine, identifier: UUID, token: str) -> dict[str, Any]:
    if len(token) != 43 or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in token
    ):
        raise ApiError(ErrorCode.NO_DATA, "公开报告不存在、已吊销或已过期")
    with engine.begin() as c:
        value = c.execute(
            text("SELECT access.report_public_read(:id,:hash)"),
            dict(id=identifier, hash=token_digest(token)),
        ).scalar_one_or_none()
    if value is None:
        raise ApiError(ErrorCode.NO_DATA, "公开报告不存在、已吊销或已过期")
    return dict(value)


PUBLIC_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


def public_html(draft: dict[str, Any]) -> str:
    """仅渲染作者选中的可见模块，不输出内部ID、查询目录或隐藏正文。"""

    def esc(value: Any) -> str:
        return escape(str(value), quote=True)

    def display(value: Any, unit: Any) -> str:
        if value is None:
            return "暂无可计算数据"
        label = {"minor": " 分", "count": " 次", "rate": "（比例）", "hours": " 小时"}.get(unit, "")
        return esc(value) + label

    metrics = {m["id"]: m for m in draft["metrics"]}
    parts = []
    for module in draft["modules"]:
        if not module["visible"]:
            continue
        cards = ""
        selected = [metrics[k] for k in module["metricIds"] if k in metrics]
        if module.get("showMetrics", True):
            cards = (
                "<dl>"
                + "".join(
                    "<dt>"
                    + esc(m["name"])
                    + "</dt><dd>"
                    + display(m["value"], m.get("unit"))
                    + (
                        "；前期 " + display(m["previous"], m.get("unit"))
                        if m.get("previous") is not None
                        else ""
                    )
                    + (
                        "；同比 " + display(m["yearAgo"], m.get("unit"))
                        if m.get("yearAgo") is not None
                        else ""
                    )
                    + "</dd>"
                    for m in selected
                )
                + "</dl>"
            )
        trend = ""
        if module["showChart"] and selected:
            # 静态表保留同一冻结版本的原始逐日值，可读且无需加载外部脚本。
            rows = []
            for point in draft["points"]:
                values = point.get("values", {})
                if isinstance(values, dict):
                    rows.append(
                        "<tr><th>"
                        + esc(point.get("date", ""))
                        + "</th>"
                        + "".join(
                            "<td>"
                            + esc(values.get(m["id"]) if values.get(m["id"]) is not None else "—")
                            + "</td>"
                            for m in selected
                        )
                        + "</tr>"
                    )
            trend = (
                "<table><caption>冻结趋势明细</caption><thead><tr><th>日期</th>"
                + "".join("<th>" + esc(m["name"]) + "</th>" for m in selected)
                + "</tr></thead><tbody>"
                + "".join(rows)
                + "</tbody></table>"
            )
        parts.append(
            "<section><h2>"
            + esc(module["title"])
            + "</h2>"
            + cards
            + '<p class="body">'
            + esc(module["text"])
            + "</p>"
            + trend
            + "</section>"
        )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        '<meta name="robots" content="noindex,nofollow">'
        '<meta http-equiv="Content-Security-Policy" content="'
        + escape(PUBLIC_CSP, quote=True)
        + '">'
        "<title>" + esc(draft["title"]) + "</title><style>"
        "body{font-family:system-ui;max-width:1000px;margin:32px auto;padding:16px}"
        ".body{white-space:pre-wrap;overflow-wrap:anywhere}td,th{padding:8px;border:1px solid #ddd}"
        "table{border-collapse:collapse}section{margin:24px 0}</style></head><body><h1>"
        + esc(draft["title"])
        + "</h1><p>"
        + esc(draft["query"]["startDate"])
        + " — "
        + esc(draft["query"]["endDate"])
        + " Asia/Shanghai</p><p>"
        + esc(draft["coverage"])
        + "</p><p>"
        + esc(draft["attribution"])
        + "</p>"
        + "".join(parts)
        + "<footer>固定发布版本 "
        + esc(draft["version"])
        + "</footer></body></html>"
    )
