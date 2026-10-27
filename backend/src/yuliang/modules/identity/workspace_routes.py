"""M0-12：本人资料与尚无平台数据时的合法空快照。"""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from starlette.responses import JSONResponse

from yuliang.modules.audit.service import record
from yuliang.modules.identity.routes import SESSION_COOKIE, service
from yuliang.modules.identity.service import digest
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1")


class ProfileChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    displayName: str = Field(min_length=1, max_length=40)

    @field_validator("displayName")
    @classmethod
    def trim_name(cls, name: str) -> str:
        name = name.strip()
        if not name or len(name.encode("utf-16-le")) > 80:
            raise ValueError("昵称需要1至40字符")
        return name


@router.patch("/users/me")
def update_profile(value: ProfileChange, request: Request) -> JSONResponse:
    instance = service(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    row = instance.row(token)
    if not row:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
    instance.require_csrf(token, request.headers.get("X-CSRF-Token", ""))
    identity = VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"])
    with business_transaction(instance.app, identity) as session:
        previous = session.execute(
            text("SELECT access.profile_for_update(:token)"), {"token": digest(token)}
        ).scalar_one()
        if previous is None:
            raise ApiError(ErrorCode.UNAUTHENTICATED, "会话不可用")
        updated = session.execute(
            text("SELECT access.update_profile(:token,:name)"),
            {"token": digest(token), "name": value.displayName},
        ).scalar_one()
        if updated:
            record(
                session,
                identity,
                {
                    "action": "profile.update",
                    "object_type": "user",
                    "object_id": identity.user_id,
                    "request_id": UUID(request.state.request_id),
                    "before_hash": instance.audit_digest("profile", previous),
                    "after_hash": instance.audit_digest("profile", value.displayName),
                    "detail": {"changed_fields": ["displayName"]},
                },
            )
    if not updated:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "会话不可用")
    return success(request, instance.session(token))


@router.get("/frontend/snapshot")
def empty_snapshot(request: Request) -> JSONResponse:
    instance = service(request)
    row = instance.row(request.cookies.get(SESSION_COOKIE))
    if not row:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
    current = instance.identity(row)
    if "snapshot.read" not in current["permissions"]:
        raise ApiError(ErrorCode.FORBIDDEN, "没有工作区读取权限")
    identity = VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"])
    with business_transaction(instance.app, identity) as session:
        tenant = session.execute(text("SELECT id,name FROM tenants")).mappings().one()
    # 仅M0初始空快照；平台镜像与实时revision在对应M1/M4卡实现，不造平台数据。
    collections: dict[str, list[dict[str, str]]] = {
        key: []
        for key in (
            "tenants",
            "shops",
            "adAccounts",
            "liveSessions",
            "plans",
            "products",
            "materials",
            "tasks",
            "riskPolicies",
            "replaySegments",
            "replayReports",
            "connectors",
        )
    }
    collections["tenants"] = [{"id": str(tenant["id"]), "name": tenant["name"]}]
    return success(
        request,
        {
            **collections,
            "schemaVersion": "command-job-v1.0",
            "revision": 1,
            "source": "API",
            "scope": {
                "shop_id": "all",
                "live_session_id": "all",
                "connector_id": "all",
                "date": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d"),
                "timezone": "Asia/Shanghai",
            },
        },
    )
