"""六阶段证据与单次灰度许可；仅描述服务端已核对的事实。"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

CONNECTOR_CONTEXTS = {
    "adq": ("tencent", "adq"),
    "wxpromo": ("tencent", "shop"),
    "beans": ("beans", "beans"),
    "wxshop": ("wxshop", "wxshop"),
    "qianchuan": ("ocean", "qianchuan"),
    "douyinshop": ("ocean", "douyinshop"),
    "juguang": ("xhs", "juguang"),
    "redshop": ("xhs", "redshop"),
}
WRITE_PERMISSIONS = {
    "create": "plan.write",
    "budget": "budget.edit",
    "bid": "plan.write",
    "pause": "plan.write",
    "resume": "plan.write",
    "goal": "goal.edit",
    "allocate": "budget.edit",
    "rules": "policy.edit",
    "review": "task.approve",
    "retry": "task.create",
    "analyze": "task.create",
    "verify": "task.create",
}


class Phase(StrEnum):
    PATH = "PATH_VERIFIED"
    SCOPE = "SCOPE_GRANTED"
    ACCOUNT = "ACCOUNT_AUTHORIZED"
    BACKEND = "BACKEND_INTEGRATED"
    SCHEMA = "SCHEMA_VERIFIED"
    EXECUTION = "EXECUTION_VERIFIED"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    binding_id: UUID
    sequence: int = Field(strict=True, ge=0, default=0)
    phase: Phase
    result: Literal["PASS", "FAIL", "UNKNOWN"]
    proof_kind: Literal[
        "DOCUMENTATION",
        "SCOPE_GRANT",
        "ACCOUNT_AUTH",
        "ADAPTER_INTEGRATION",
        "READ_SCHEMA",
        "WRITE_SCHEMA",
        "READ_RESULT",
        "MATCHED_RECEIPT",
    ]
    proof_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,255}$")
    checked_by: str = Field(min_length=1, max_length=120)
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def time_window(self) -> Self:
        if self.expires_at <= self.checked_at:
            raise ValueError("证据有效期必须晚于核验时间")
        return self


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    tenant_id: int = Field(strict=True, gt=0)
    account_id: UUID
    connector_id: str
    provider: str
    product_line: str
    action: Literal[
        "create",
        "budget",
        "bid",
        "pause",
        "resume",
        "review",
        "analyze",
        "goal",
        "allocate",
        "verify",
        "retry",
        "rules",
    ]
    operation_kind: Literal["READ", "WRITE"]
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_id: str = Field(pattern=r"^[0-9a-f]{20}$")
    revision: int = Field(strict=True, gt=0)
    http_method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    normalized_path: str = Field(pattern=r"^/[^\s?#]*$", max_length=2000)
    permission_code: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def context(self) -> Self:
        if CONNECTOR_CONTEXTS.get(self.connector_id) != (self.provider, self.product_line):
            raise ValueError("连接器、provider和产品线不匹配")
        if self.operation_kind == "WRITE" and self.http_method in {"GET", "HEAD", "OPTIONS"}:
            raise ValueError("写操作不能使用只读方法")
        if self.operation_kind == "READ" and self.http_method not in {
            "GET",
            "HEAD",
            "OPTIONS",
            "POST",
        }:
            raise ValueError("只读操作不能使用修改方法")
        if (
            self.operation_kind == "WRITE"
            and self.permission_code != WRITE_PERMISSIONS[self.action]
        ):
            raise ValueError("写操作必须绑定对应动作权限")
        return self


class ValidationPermit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    binding_id: UUID
    requester_id: UUID
    approved_by: UUID
    approval_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,255}$")
    request_key: UUID
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_amount_minor: int = Field(strict=True, ge=0, le=9007199254740991)
    starts_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def distinct_approval(self) -> Self:
        if self.requester_id == self.approved_by or self.expires_at <= self.starts_at:
            raise ValueError("验证许可需他人批准及有效时间窗")
        return self


def proof_matches(binding: Binding, evidence: Evidence) -> bool:
    kinds = {
        Phase.PATH: "DOCUMENTATION",
        Phase.SCOPE: "SCOPE_GRANT",
        Phase.ACCOUNT: "ACCOUNT_AUTH",
        Phase.BACKEND: "ADAPTER_INTEGRATION",
        Phase.SCHEMA: "READ_SCHEMA" if binding.operation_kind == "READ" else "WRITE_SCHEMA",
        Phase.EXECUTION: "READ_RESULT" if binding.operation_kind == "READ" else "MATCHED_RECEIPT",
    }
    return evidence.binding_id == binding.id and evidence.proof_kind == kinds[evidence.phase]


def aggregate(
    binding: Binding,
    evidence: list[Evidence],
    *,
    now: datetime,
    permitted: bool = True,
    applicable: bool = True,
    account_authorized: bool = True,
) -> dict[str, Any]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("聚合时间必须带时区")
    latest: dict[Phase, Evidence] = {}
    for candidate in evidence:
        previous = latest.get(candidate.phase)
        if (
            previous is not None
            and (candidate.checked_at, candidate.sequence)
            == (previous.checked_at, previous.sequence)
            and candidate != previous
        ):
            raise ValueError("同阶段相同核验时间及序号出现冲突证据")
        if previous is None or (candidate.checked_at, candidate.sequence) > (
            previous.checked_at,
            previous.sequence,
        ):
            latest[candidate.phase] = candidate
    stages: dict[str, dict[str, object]] = {}
    valid, first_missing = True, None
    predecessor: tuple[datetime, int] | None = None
    for phase in Phase:
        item = latest.get(phase)
        effective = bool(
            valid
            and item
            and item.result == "PASS"
            and item.checked_at <= now < item.expires_at
            and proof_matches(binding, item)
            and (predecessor is None or (item.checked_at, item.sequence) >= predecessor)
        )
        if phase == Phase.ACCOUNT and not account_authorized:
            effective = False
        stages[phase.value] = {
            "effective": effective,
            "proofRef": item.proof_ref if item else None,
            "checkedAt": item.checked_at.isoformat() if item else None,
        }
        if not effective and valid:
            first_missing = phase
        if effective and item:
            predecessor = (item.checked_at, item.sequence)
        valid = effective
    if not applicable:
        status, reason = "NOT_APPLICABLE", "当前上下文不适用该动作"
    elif not permitted:
        status, reason = "FORBIDDEN", "当前成员没有动作或对象范围权限"
    elif binding.provider in {"ocean", "xhs", "beans"}:
        status, reason = "NOT_INTEGRATED", "本轮尚无该连接器的正式集成依据"
    elif first_missing is None:
        status, reason = "AUTHORIZED", "对应读写证据完整且仍有效"
    elif first_missing in {Phase.SCOPE, Phase.ACCOUNT}:
        status, reason = "UNAUTHORIZED", "应用scope或账户授权缺失、失效或未通过"
    else:
        missing = latest.get(first_missing)
        failed = missing is not None and (
            missing.result == "FAIL"
            or (missing.result == "PASS" and not proof_matches(binding, missing))
        )
        status, reason = (
            ("ERROR", "证据核验失败或类型不匹配")
            if failed
            else ("NOT_INTEGRATED", "路径、适配器、参数或对应真实结果证据尚未齐备")
        )
    ready = (
        binding.operation_kind == "WRITE"
        and status == "NOT_INTEGRATED"
        and first_missing == Phase.EXECUTION
        and Phase.EXECUTION not in latest
        and permitted
        and applicable
        and binding.provider == "tencent"
    )
    return {
        "status": status,
        "reason": reason,
        "stages": stages,
        "validationReady": ready,
        "bindingId": str(binding.id),
        "version": binding.revision,
        "operations": [
            {
                "action": binding.action,
                "kind": binding.operation_kind,
                "catalogId": binding.catalog_id,
            }
        ]
        if status == "AUTHORIZED"
        else [],
    }
