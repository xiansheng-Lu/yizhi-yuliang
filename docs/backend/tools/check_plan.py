"""不依赖后端运行时的计划检查：卡片完整性、依赖、顺序、父任务及门禁覆盖。"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARCH = ROOT / "docs/backend/architecture"
ID = r"(?:R00-\d{2}|M[0-5]-\d{2}[a-z]?|G(?:-MVP|[0-5]))"


def check() -> dict[str, object]:
    plan = (ARCH / "20-后端逐任务开发与验收卡.md").read_text(encoding="utf-8")
    overview = (ARCH / "19-细化排期与验收执行方案.md").read_text(encoding="utf-8")
    original = (ARCH / "17-任务排期与验收标准.md").read_text(encoding="utf-8")
    matches = list(re.finditer(rf"^### ({ID})$", plan, re.M))
    names = [m[1] for m in matches]
    assert len(names) == len(set(names)) == 156, "节点重复或数量变化"
    nodes = {}
    for i, match in enumerate(matches):
        block = plan[match.end(): matches[i + 1].start() if i + 1 < len(matches) else len(plan)]
        dependency = re.search(r"^(?:- 直接依赖：|直接前置：)(.*)$", block, re.M)
        assert dependency, f"{match[1]}缺直接依赖"
        deps = re.findall(rf"\[({ID})\]\(#", dependency[1])
        nodes[match[1]] = deps
        if not match[1].startswith("G"):
            # 最后一个叶卡之后包含映射表；仅取卡片正文。
            card = block.split("\n## ", 1)[0]
            assert len(re.findall(r"^[123]\. ", card, re.M)) == 3, f"{match[1]}的DoD不完整"
            for field in ("负责人：BE", "改动范围：", "交付物：", "验收映射：", "外部条件：", "验证类型："):
                assert field in card, f"{match[1]}缺{field}"
    queue = plan.split("## 3. ", 1)[1].split("## 4. ", 1)[0]
    rows = re.findall(rf"^\| (\d{{3}}) \| \[({ID})\].*?\| ([^\n]*) \|$", queue, re.M)
    assert [int(r[0]) for r in rows] == list(range(1, 157)), "队列编号不连续"
    seen = set()
    for _, name, dependency in rows:
        assert name not in seen and name in nodes, f"队列节点错误{name}"
        assert set(nodes[name]) == set(re.findall(rf"\[({ID})\]\(#", dependency)), f"{name}队列依赖与正文不一致"
        assert set(nodes[name]) <= seen, f"{name}依赖未知、循环或未排在前面"
        seen.add(name)
    assert seen == set(nodes)
    parents = set(re.findall(r"^\| (M[0-5]-\d{2}) \|", original, re.M))
    mapping = plan.split("## 11. ", 1)[1].split("## 12. ", 1)[0]
    mapped = set(re.findall(r"^\| `?(M[0-5]-\d{2})`? \|", mapping, re.M))
    assert len(parents) == 101 and parents == mapped, "原101任务映射缺失"
    for name in seen:
        if name.startswith("M"):
            assert name[:5] in parents, f"未知父任务{name}"
            assert f"[{name}](#" in mapping, f"{name}未映射"
    reachable = set()

    def visit(name: str) -> None:
        for dep in nodes[name]:
            if dep not in reachable:
                reachable.add(dep)
                visit(dep)

    visit("G5")
    assert reachable == seen - {"G5"}, "发布门禁遗漏任务"
    assert len(re.findall(r"^\| A\d{2} \|", overview, re.M)) == 42, "A验收数量变化"
    assert set(re.findall(r"^\| (F\d{2})\b", overview, re.M)) == {f"F{i:02}" for i in range(14)}
    for source in (ARCH / "19-细化排期与验收执行方案.md", ARCH / "20-后端逐任务开发与验收卡.md"):
        for target in re.findall(r"\]\(([^)]+)\)", source.read_text(encoding="utf-8")):
            if target.startswith(("http", "#")):
                continue
            assert (source.parent / target.split("#")[0]).exists(), f"失效链接{target}"
    return {"result": "PASS", "cards": 149, "gates": 7, "queue_nodes": 156, "parents": 101, "acceptance_cases": 42, "frontend_cards": 14, "release_coverage": "all cards"}


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=2))
