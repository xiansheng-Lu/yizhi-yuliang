"""有界模板定义、DAG与开线请求；不开放执行能力。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class TemplateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    productLines: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=32
    )
    steps: list[StepDefinition] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def dag(self) -> "TemplateDefinition":
        nodes = {step.code: step for step in self.steps}
        if len(nodes) != len(self.steps):
            raise ValueError("步骤代码不可重复")
        indegree = dict.fromkeys(nodes, 0)
        children: dict[str, list[str]] = {key: [] for key in nodes}
        for step in self.steps:
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
