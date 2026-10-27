"""身份HTTP契约；默认不开启，F01联合接线后显式配置开放。"""

import re
from typing import Literal, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from starlette.responses import JSONResponse

from yuliang.modules.identity.service import IdentityService
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode
from yuliang.settings import Settings

router = APIRouter(prefix="/v1/auth")
SESSION_COOKIE = "yuliang_session"
PREAUTH_COOKIE = "yuliang_preauth"


class PasswordLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    email: str = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)
    method: Literal["password"] = "password"

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("邮箱格式不正确")
        return value


def exact_origin(request: Request) -> None:
    settings: Settings = request.app.state.settings
    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        parsed = urlsplit(referer or str(request.base_url))
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise ApiError(ErrorCode.FORBIDDEN, "请求来源未获允许")
    if origin not in settings.allowed_origins:
        raise ApiError(ErrorCode.FORBIDDEN, "请求来源未获允许")


def service(request: Request) -> IdentityService:
    exact_origin(request)
    return cast(IdentityService, request.app.state.identity_service)


def set_cookie(response: JSONResponse, name: str, value: str, age: int, settings: Settings) -> None:
    response.set_cookie(
        name,
        value,
        max_age=age,
        httponly=True,
        secure=settings.environment in ("prod", "staging"),
        samesite="lax",
        path="/",
    )


@router.get("/csrf")
def csrf(request: Request) -> JSONResponse:
    token, expires, preauth = service(request).csrf(request.cookies.get(SESSION_COOKIE))
    response = success(request, {"token": token, "expiresAt": expires.isoformat()})
    if preauth:
        set_cookie(response, PREAUTH_COOKIE, preauth, 600, request.app.state.settings)
    return response


@router.post("/login")
def login(value: PasswordLogin, request: Request) -> JSONResponse:
    identity, token, age = service(request).login(
        value.email,
        value.password.get_secret_value(),
        request.cookies.get(PREAUTH_COOKIE, ""),
        request.headers.get("X-CSRF-Token", ""),
        request.client.host if request.client else "unknown",
        request.state.request_id,
    )
    response = success(request, identity)
    set_cookie(response, SESSION_COOKIE, token, age, request.app.state.settings)
    response.delete_cookie(PREAUTH_COOKIE, path="/")
    return response


@router.get("/session")
def session(request: Request) -> JSONResponse:
    return success(request, service(request).session(request.cookies.get(SESSION_COOKIE)))


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    service(request).logout(
        request.cookies.get(SESSION_COOKIE, ""),
        request.headers.get("X-CSRF-Token", ""),
        request.client.host if request.client else "unknown",
        request.state.request_id,
    )
    response = success(request, None)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(PREAUTH_COOKIE, path="/")
    return response
