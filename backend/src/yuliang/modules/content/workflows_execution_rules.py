"""仅按冻结DAG计算依赖，禁止目录名/确认标记推测。"""

from collections.abc import Mapping

from yuliang.modules.content.workflows_models import TemplateDefinition


def downstream(template: TemplateDefinition, code: str) -> set[str]:
    children: dict[str, list[str]] = {step.code: [] for step in template.steps}
    if code not in children:
        raise ValueError("步骤不存在")
    for step in template.steps:
        for dep in step.dependsOn:
            children[dep].append(step.code)
    result = {code}
    queue = [code]
    while queue:
        for child in children[queue.pop()]:
            if child not in result:
                result.add(child)
                queue.append(child)
    return result


def satisfied(template: TemplateDefinition, code: str, statuses: Mapping[str, str]) -> bool:
    step = next(step for step in template.steps if step.code == code)
    return all(
        statuses.get(dep) == "DONE"
        or (
            template.schemaVersion == 2
            and dep in step.optionalDependsOn
            and statuses.get(dep) == "SKIPPED"
        )
        for dep in step.dependsOn
    )
