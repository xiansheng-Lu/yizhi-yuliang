"""不可变版本清单、人工标注守卫和离线结果比较；不实现在线指令解析。"""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from yuliang.modules.aikernel.models import ModelSpec

type Action = Literal[
    "CHANGE_DAILY_BUDGET",
    "CHANGE_BID",
    "SET_ROI_TARGET",
    "PAUSE_AD_PLAN",
    "RESUME_AD_PLAN",
    "REQUEST_LIVE_START",
    "REQUEST_LIVE_END",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Money(StrictModel):
    kind: Literal["MONEY"]
    currency: Literal["CNY"]
    amount_minor: int = Field(strict=True, gt=0, le=9007199254740991)


class Ratio(StrictModel):
    kind: Literal["RATIO"]
    value: float = Field(strict=True, gt=0, allow_inf_nan=False)


class Boolean(StrictModel):
    kind: Literal["BOOLEAN"]
    value: bool = Field(strict=True)


class Handoff(StrictModel):
    kind: Literal["HANDOFF"]
    value: Literal["START", "END"]


type JobValue = Annotated[Money | Ratio | Boolean | Handoff, Field(discriminator="kind")]


class Interpretation(StrictModel):
    kind: Literal["QUERY", "ACTION", "UNRECOGNIZED"]
    text: str = Field(default="", max_length=500)
    action_type: Action | None = None
    new_value: JobValue | None = None
    target_type: Literal["AD_PLAN", "LIVE_SESSION"] | None = None
    candidate_ids: tuple[UUID, ...] = Field(default=(), max_length=20)
    live_session_id: UUID | None = None

    @model_validator(mode="after")
    def only_allowed_shape(self) -> "Interpretation":
        if self.kind != "ACTION":
            if any(
                value is not None for value in (self.action_type, self.new_value, self.target_type)
            ):
                raise ValueError("查询或拒绝不能含动作")
            if self.candidate_ids or (self.kind == "UNRECOGNIZED" and self.live_session_id):
                raise ValueError("非动作不能选择动作对象")
            return self
        if not self.candidate_ids or len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("动作需有不重复的候选对象，零候选必须澄清")
        if self.live_session_id is not None:
            raise ValueError("动作不能混入查询字段")
        action, value = self.action_type, self.new_value
        if action in ("CHANGE_DAILY_BUDGET", "CHANGE_BID"):
            valid = isinstance(value, Money)
        elif action == "SET_ROI_TARGET":
            valid = isinstance(value, Ratio)
        elif action in ("PAUSE_AD_PLAN", "RESUME_AD_PLAN"):
            valid = isinstance(value, Boolean) and value.value == (action == "RESUME_AD_PLAN")
        else:
            valid = (
                isinstance(value, Handoff)
                and value.value == ("START" if action == "REQUEST_LIVE_START" else "END")
                and action in ("REQUEST_LIVE_START", "REQUEST_LIVE_END")
            )
        target = "LIVE_SESSION" if isinstance(value, Handoff) else "AD_PLAN"
        if not valid or self.target_type != target:
            raise ValueError("动作、值与对象类型不一致")
        return self

    def semantic(self) -> dict[str, Any]:
        value = self.model_dump(mode="json", exclude={"text"}, exclude_none=True)
        value["candidate_ids"] = sorted(value["candidate_ids"])
        return value


class CandidateObject(StrictModel):
    id: UUID
    target_type: Literal["AD_PLAN", "LIVE_SESSION"]
    name: str


class CaseContext(StrictModel):
    candidates: tuple[CandidateObject, ...]
    allowed_actions: tuple[Action, ...]


class HumanReview(StrictModel):
    role: Literal["BE", "FE"]
    name: str = Field(min_length=1)
    reviewed_on: date
    baseline: str = Field(pattern=r"^[0-9a-f]{40}$")
    source: str = Field(min_length=1)
    case_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GoldenCase(StrictModel):
    id: str = Field(pattern=r"^CP-[0-9]{3}$")
    tags: tuple[
        Literal[
            "legal",
            "ambiguous",
            "mixed",
            "units",
            "unauthorized",
            "injection",
            "invalid",
        ],
        ...,
    ] = Field(min_length=1)
    input: str = Field(min_length=1, max_length=500)
    context: CaseContext
    expected: Interpretation
    reason: str = Field(min_length=1)
    reviews: tuple[HumanReview, ...] = ()

    @model_validator(mode="after")
    def expected_has_authorized_context(self) -> "GoldenCase":
        validate_context(self.expected, self.context)
        if len({review.role for review in self.reviews}) != len(self.reviews):
            raise ValueError("同端不能重复签署")
        for review in self.reviews:
            if review.case_hash != self.case_hash():
                raise ValueError("样例变化使原人工标注失效")
        return self

    def case_hash(self) -> str:
        return digest_json(self.model_dump(mode="json", exclude={"reviews"}))


def digest_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def validate_context(value: Interpretation, context: CaseContext) -> None:
    objects = {candidate.id: candidate.target_type for candidate in context.candidates}
    if value.kind == "ACTION":
        if value.action_type not in context.allowed_actions:
            raise ValueError("动作不在已授权列表")
        if any(objects.get(identifier) != value.target_type for identifier in value.candidate_ids):
            raise ValueError("候选越过服务端提供的对象范围")
    if value.live_session_id and objects.get(value.live_session_id) != "LIVE_SESSION":
        raise ValueError("查询场次不在授权集合")


class FrozenRoute(StrictModel):
    """与STARTED账本的model_snapshot同形，仅服务端引用，不含密钥。"""

    config_id: UUID
    revision: int = Field(strict=True, gt=0)
    primary: ModelSpec
    backup: ModelSpec | None
    selected: ModelSpec
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_schema_ref: str

    @model_validator(mode="after")
    def selected_is_frozen_route(self) -> "FrozenRoute":
        if self.selected not in (self.primary, self.backup):
            raise ValueError("所选模型不属于冻结路由")
        return self


class Manifest(StrictModel):
    prompt_key: Literal["command_parse"]
    prompt_version: int = Field(strict=True, gt=0)
    status: Literal["CANDIDATE"]
    output_schema_ref: Literal["command-parse-candidate-v1"]
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters: dict[str, Any]
    change_reason: str = Field(min_length=1)
    preserved_case_ids: tuple[str, ...]
    case_changes: dict[str, str] = Field(default_factory=dict)


class PromptBundle(StrictModel):
    manifest: Manifest
    template: str
    cases: tuple[GoldenCase, ...]

    def pending_review_ids(self) -> list[str]:
        return [case.id for case in self.cases if {r.role for r in case.reviews} != {"BE", "FE"}]

    def require_human_labels(self) -> None:
        if self.pending_review_ids():
            raise ValueError("人工预期标注未齐，不可当作已确认黄金集或发布通行证")

    def bind_route(self, snapshot: dict[str, Any]) -> FrozenRoute:
        route = FrozenRoute.model_validate(snapshot)
        if route.prompt_hash != self.manifest.template_sha256 or (
            route.output_schema_ref != self.manifest.output_schema_ref
        ):
            raise ValueError("账本中的提示词或结构版本与候选包不符")
        return route

    def compare(self, predictions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        known = {case.id for case in self.cases}
        if set(predictions) - known:
            raise ValueError("预测包含未登记样例")
        failed: list[str] = []
        for case in self.cases:
            try:
                result = Interpretation.model_validate(predictions.get(case.id))
                validate_context(result, case.context)
                equal = result.semantic() == case.expected.semantic()
            except ValueError:
                equal = False
            if not equal:
                failed.append(case.id)
        return {
            "cases": len(self.cases),
            "matched": len(self.cases) - len(failed),
            "failed_ids": failed,
            "human_labels_confirmed": not self.pending_review_ids(),
            "release_allowed": False,
        }  # CANDIDATE报告不执行发布或激活。


def validate_successor(previous: PromptBundle, candidate: PromptBundle) -> None:
    if candidate.manifest.prompt_version <= previous.manifest.prompt_version:
        raise ValueError("修改必须新增更高版本")
    old = {case.id: case for case in previous.cases}
    new = {case.id: case for case in candidate.cases}
    if old.keys() - new.keys():
        raise ValueError("不能删除历史或失败样例")
    for identifier, case in old.items():
        if case.case_hash() != new[identifier].case_hash() and not (
            candidate.manifest.case_changes.get(identifier, "").strip()
        ):
            raise ValueError("改变样例预期需逐项记录理由")


def load_bundle(directory: Path | None = None) -> PromptBundle:
    """显式目录仅供测试/版本维护；业务默认固定随包版本，不从客户端路径加载。"""
    root = directory or Path(__file__).with_name("v1")
    manifest = Manifest.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))
    template = (root / "template.txt").read_text(encoding="utf-8")
    content = (root / "cases.json").read_text(encoding="utf-8")
    if hashlib.sha256(template.encode()).hexdigest() != manifest.template_sha256:
        raise ValueError("冻结提示词内容已变化，需新版本和修改理由")
    if hashlib.sha256(content.encode()).hexdigest() != manifest.cases_sha256:
        raise ValueError("冻结样例已变化，需新版本和修改理由")
    cases = TypeAdapter(tuple[GoldenCase, ...]).validate_json(content)
    identifiers = [case.id for case in cases]
    if len(identifiers) != len(set(identifiers)) or set(manifest.preserved_case_ids) != set(
        identifiers
    ):
        raise ValueError("样例删除/重复或未保留历史失败项")
    tags = {tag for case in cases for tag in case.tags}
    if not {"legal", "ambiguous", "mixed", "units", "unauthorized", "injection"} <= tags:
        raise ValueError("缺少必测解析场景")
    return PromptBundle(manifest=manifest, template=template, cases=cases)
