"""认证管理与无Cookie能力读取分离；客户端不能覆盖公开源或访问期限。"""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import SecretStr
from starlette.responses import HTMLResponse, JSONResponse

from yuliang.modules.analysis.publications import (
    PUBLIC_CSP,
    PublicationCreate,
    PublicationService,
    limit_public_read,
    public_html,
    read_public,
)
from yuliang.modules.analysis.routes import analysis
from yuliang.modules.identity.service import IdentityService
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter()


def publications(request: Request) -> PublicationService:
    current = analysis(request)
    return PublicationService(current.engine, current.identity, request.app.state.settings)


@router.post("/v1/business-analysis/drafts/{identifier}/publications")
def publish(identifier: UUID, value: PublicationCreate, request: Request) -> JSONResponse:
    return success(request, publications(request).create(identifier, value))


@router.get("/v1/business-analysis/publications")
def publication_list(request: Request) -> JSONResponse:
    return success(request, publications(request).list())


@router.get("/v1/business-analysis/publications/{identifier}")
def publication_get(identifier: UUID, request: Request) -> JSONResponse:
    return success(request, publications(request).get(identifier))


@router.get("/v1/business-analysis/publications/{identifier}/export")
def publication_export(identifier: UUID, request: Request) -> HTMLResponse:
    published = publications(request).get(identifier)
    return HTMLResponse(
        public_html(published["snapshot"]),
        headers={
            "Content-Security-Policy": PUBLIC_CSP,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'attachment; filename="report.html"',
        },
    )


@router.post("/v1/business-analysis/publications/{identifier}/revoke")
def publication_revoke(identifier: UUID, request: Request) -> JSONResponse:
    publications(request).revoke(identifier)
    return success(request, dict(revoked=True))


@router.get("/reports/public/{identifier}")
def public_report(identifier: UUID, request: Request) -> HTMLResponse:
    # 公开能力读取不消费Cookie，不套用认证入口的同源/CSRF限制。
    instance = cast(IdentityService, request.app.state.identity_service)
    settings = request.app.state.settings
    if settings.report_public_origin is None:
        raise ApiError(ErrorCode.NO_DATA, "公开报告入口尚未启用")
    ip = request.client.host if request.client else "unknown"
    limit_public_read(
        instance.cache,
        instance.audit_digest("report-read-ip", ip),
        settings.report_public_reads_per_minute,
    )
    secret = getattr(request.state, "report_public_token", None)
    token = secret.get_secret_value() if isinstance(secret, SecretStr) else ""
    draft = read_public(instance.app, identifier, token)
    headers = {
        "Content-Security-Policy": PUBLIC_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
        "X-Robots-Tag": "noindex, nofollow",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Content-Disposition": 'inline; filename="report.html"',
    }
    return HTMLResponse(public_html(draft), headers=headers)
