"""应用装配入口。uvicorn yuliang.main:create_app --factory。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from yuliang.api.operations import router as operations_router
from yuliang.infrastructure.mail import MailSender
from yuliang.platform.envelope import success
from yuliang.platform.errors import (
    ApiError,
    api_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from yuliang.platform.middleware import RequestContextMiddleware
from yuliang.platform.probes import ProbeService
from yuliang.platform.security.oauth_callback import OAuthCallbackPrivacy
from yuliang.platform.security.public_report import PublicReportPrivacy
from yuliang.settings import Settings


def create_app(settings: Settings | None = None, mail_sender: MailSender | None = None) -> FastAPI:
    if settings is None:
        try:
            settings = Settings()
        except ValidationError as error:
            fields = sorted({str(item["loc"][0]) for item in error.errors()})
            raise RuntimeError("后端配置缺失或不合法：" + ", ".join(fields)) from None
    structlog.configure(processors=[structlog.processors.JSONRenderer(ensure_ascii=False)])

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.probes = ProbeService(app.state.settings)
        if app.state.settings.identity_api_enabled:
            from yuliang.modules.identity.service import IdentityService

            app.state.identity_service = IdentityService(
                app.state.settings, mail_sender=mail_sender
            )
        app.state.platform_runtime = None
        app.state.blob_store = None
        try:
            if app.state.settings.blob_enabled:
                from yuliang.infrastructure.blob import BlobStore

                app.state.blob_store = BlobStore.from_settings(app.state.settings)
            if app.state.settings.platform_resources_file is not None:
                from yuliang.modules.connectors.runtime import PlatformRuntime

                app.state.platform_runtime = PlatformRuntime.load(
                    app.state.identity_service.app,
                    app.state.settings,
                    app.state.settings.platform_resources_file,
                )
            yield
        finally:
            if app.state.blob_store is not None:
                app.state.blob_store.close()
            if app.state.platform_runtime is not None:
                app.state.platform_runtime.close()
            app.state.probes.close()
            if app.state.settings.identity_api_enabled:
                app.state.identity_service.close()

    app = FastAPI(
        title="易致驭量后端",
        version="0.1.0",
        debug=False,
        lifespan=lifespan,
        docs_url=None if settings.environment == "prod" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.environment == "prod" else "/openapi.json",
    )
    app.state.settings = settings
    app.include_router(operations_router)
    if settings.identity_api_enabled:
        from yuliang.api.configuration import router as configuration_router
        from yuliang.api.connections import router as connection_router
        from yuliang.api.events import router as event_router
        from yuliang.api.integration_query_routes import router as integration_query_router
        from yuliang.api.integrations import router as integration_router
        from yuliang.api.platform_authorization import router as platform_router
        from yuliang.modules.analysis.publication_routes import router as publication_router
        from yuliang.modules.analysis.routes import router as analysis_router
        from yuliang.modules.content.account_links_routes import (
            router as content_account_links_router,
        )
        from yuliang.modules.content.ai_content_routes import router as ai_content_router
        from yuliang.modules.content.assets_routes import router as content_assets_router
        from yuliang.modules.content.flows_routes import router as content_flows_router
        from yuliang.modules.content.generation_routes import router as generation_router
        from yuliang.modules.content.imports_routes import router as content_imports_router
        from yuliang.modules.content.routes import router as content_router
        from yuliang.modules.content.uploads_routes import router as content_uploads_router
        from yuliang.modules.content.workflows_execution_routes import (
            router as workflows_execution_router,
        )
        from yuliang.modules.content.workflows_routes import router as content_workflows_router
        from yuliang.modules.identity.routes import router
        from yuliang.modules.identity.workspace_routes import router as workspace_router
        from yuliang.modules.workspace.routes import router as history_router

        app.include_router(router)
        app.include_router(workspace_router)
        app.include_router(history_router)
        app.include_router(platform_router)
        app.include_router(connection_router)
        app.include_router(configuration_router)
        app.include_router(integration_router)
        app.include_router(integration_query_router)
        app.include_router(event_router)
        app.include_router(analysis_router)
        app.include_router(publication_router)
        app.include_router(content_router)
        app.include_router(content_uploads_router)
        app.include_router(content_assets_router)
        app.include_router(content_flows_router)
        app.include_router(content_workflows_router)
        app.include_router(workflows_execution_router)
        app.include_router(content_imports_router)
        app.include_router(content_account_links_router)
        app.include_router(ai_content_router)
        app.include_router(generation_router)
    if settings.admin_api_enabled:
        from yuliang.admin.routes import router as admin_router

        app.include_router(admin_router)
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(OAuthCallbackPrivacy)
    app.add_middleware(PublicReportPrivacy)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-CSRF-Token",
            "Idempotency-Key",
            "X-Shop-Id",
            "X-Yuliang-Client",
            "Last-Event-ID",
        ],
        expose_headers=["X-Request-Id", "Retry-After"],
    )

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request) -> JSONResponse:
        # 仅进程存活；依赖与降级状态通过受控来源readyz/metrics读取。
        return success(request, {"alive": True})

    return app
