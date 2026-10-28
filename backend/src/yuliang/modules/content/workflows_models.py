"""有界模板定义、DAG与开线请求；不开放执行能力。"""

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)


class StepDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["MANUAL", "AI_ASSISTED", "GENERATION", "REVIEW"]
    dependsOn: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]] = Field(
        default_factory=list, max_length=100
    )
    requiresHumanConfirm: bool = True
    inputs: list[Annotated[str, Field(max_length=128)]] = Field(default_factory=list, max_length=32)
    documentKind: Literal["planning", "scripts", "graphics", "videos", "editing"] = "planning"
    optional: bool = False
    optionalDependsOn: list[str] = Field(default_factory=list, max_length=100)


class TemplateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    productLines: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=32
    )
    steps: list[StepDefinition] = Field(min_length=1, max_length=100)
    schemaVersion: Literal[1, 2] = 1

    @model_validator(mode="before")
    @classmethod
    def legacy(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("schemaVersion", 1) == 1:
            for step in value.get("steps", []):
                if isinstance(step, dict) and {"optional", "optionalDependsOn"} & step.keys():
                    raise ValueError("旧版模板不接受可选步骤字段，请追加schemaVersion=2的新版本")
        return value

    @model_serializer(mode="wrap")
    def serialized(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value: dict[str, Any] = handler(self)
        if self.schemaVersion == 1:
            value.pop("schemaVersion", None)
            for step in value["steps"]:
                step.pop("optional", None)
                step.pop("optionalDependsOn", None)
        return value

    @model_validator(mode="after")
    def dag(self) -> "TemplateDefinition":
        nodes = {step.code: step for step in self.steps}
        if len(nodes) != len(self.steps):
            raise ValueError("步骤代码不可重复")
        indegree = dict.fromkeys(nodes, 0)
        children: dict[str, list[str]] = {key: [] for key in nodes}
        for step in self.steps:
            if len(set(step.optionalDependsOn)) != len(step.optionalDependsOn) or not set(
                step.optionalDependsOn
            ) <= set(step.dependsOn):
                raise ValueError("可选依赖必须为本步骤依赖的唯一子集")
            if len(set(step.dependsOn)) != len(step.dependsOn):
                raise ValueError("步骤依赖不可重复")
            for dep in step.dependsOn:
                if dep not in nodes:
                    raise ValueError("步骤依赖不存在")
                indegree[step.code] += 1
                children[dep].append(step.code)
        ready = [key for key, count in indegree.items() if not count]
        count = 0
        while ready:
            current = ready.pop()
            count += 1
            for child in children[current]:
                indegree[child] -= 1
                if not indegree[child]:
                    ready.append(child)
        if count != len(nodes):
            raise ValueError("步骤依赖存在环")
        return self


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    templateId: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    productId: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    creationKey: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str = Field(default="商品内容策划", min_length=1, max_length=120)

    @model_validator(mode="after")
    def identifiers(self) -> "RunCreate":
        from uuid import UUID

        UUID(self.templateId)
        UUID(self.productId)
        return self
