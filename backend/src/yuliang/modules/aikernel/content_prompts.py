"""内容专用DRAFT提示资产；保持command_parse注册器完全独立。"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yuliang.modules.aikernel.prompts.registry import FrozenRoute
from yuliang.modules.content.ai_content_models import Scenario
from yuliang.modules.content.models import Kind


@dataclass(frozen=True)
class ContentPrompt:
    scenario: Scenario
    version: int
    template: str
    schema: str
    kind: Kind
    field: str

    @property
    def prompt_key(self) -> str:
        return self.scenario

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.template.encode()).hexdigest()

    def bind(self, snapshot: dict[str, Any]) -> FrozenRoute:
        route = FrozenRoute.model_validate(snapshot)
        if route.prompt_hash != self.digest or route.output_schema_ref != self.schema:
            raise ValueError("内容提示或输出schema与冻结路由不一致")
        return route


def load_content_prompt(scenario: Scenario) -> ContentPrompt:
    directory = Path(__file__).with_suffix("")
    # 只增加新prompt_key资源，不重写已发布三场景v1模板/hash。
    root = directory / "topic" / "v1" if scenario == "content_topic" else directory / "v1"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    definition = manifest["scenarios"][scenario]
    base = (root / "template.txt").read_text(encoding="utf-8-sig")
    suffix = (
        "只返回{body:字符串}。"
        if definition["field"] == "body"
        else (
            "只返回{scenes:[{title:字符串,narration:字符串,visual:字符串,duration:秒数}]}，"
            "1至20镜，每镜0.1至600秒，不输出id。"
        )
    )
    return ContentPrompt(
        scenario,
        manifest["version"],
        base + "\n场景：" + scenario + "\n" + suffix,
        definition["schema"],
        definition["kind"],
        definition["field"],
    )
