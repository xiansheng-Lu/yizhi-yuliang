"""任务台账守卫：不把技术PASS、缺签字、缺证据或缺依赖记为DONE。"""

import argparse
import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "docs/backend/evidence"
PLAN = ROOT / "docs/backend/architecture/20-后端逐任务开发与验收卡.md"
STATUSES = {
    "IN_PROGRESS",
    "IMPLEMENTED",
    "TECH_VERIFIED",
    "WAITING_REVIEW",
    "BLOCKED",
    "DONE",
}


def validate(data: dict) -> None:
    plan = PLAN.read_text(encoding="utf-8")
    blocks = dict(
        re.findall(
            r"^### (R00-\d{2}|M[0-5]-\d{2}[a-z]?|G(?:-MVP|[0-5]))\n(.*?)(?=^### |\Z)",
            plan,
            re.MULTILINE | re.DOTALL,
        )
    )
    assert data["defaultTaskStatus"] == "NOT_STARTED"
    assert data["defaultGateStatus"] == "NOT_PASSED"
    assert data["currentFocus"] in blocks
    tasks = data["tasks"]
    for name, task in tasks.items():
        assert name in blocks and not name.startswith("G"), f"未知任务{name}"
        assert task["owner"] == "BE" and task["status"] in STATUSES, (
            f"{name}状态/负责人非法"
        )
        if "evidence" in task:
            path = (EVIDENCE / task["evidence"]).resolve()
            assert path.is_relative_to(EVIDENCE.resolve()) and path.is_file(), (
                f"{name}证据缺失"
            )
        if task["status"] != "DONE":
            continue
        assert "evidence" in task, f"{name}DONE缺证据"
        assert task["technicalVerification"]["result"] == "PASS", f"{name}技术未通过"
        assert {item["item"] for item in task["dod"]} == {1, 2, 3}, f"{name}DoD缺失"
        assert all(item["technical"] == "PASS" for item in task["dod"]), (
            f"{name}DoD未通过"
        )
        assert not task.get("blockedBy"), f"{name}存在阻塞"
        review = task["review"]
        for role in ("backend", "frontend"):
            if role == "frontend" and review[role] == "NOT_APPLICABLE":
                assert not name.startswith("R00") and review.get("frontendReason"), (
                    "规格卡需双端复核"
                )
                continue
            assert review[role] == "PASS", f"{name}{role}未签字"
            record = review["records"][role]
            assert all(
                record.get(key) for key in ("name", "date", "baseline", "source")
            ), "具名记录缺项"
            assert re.fullmatch(r"[0-9a-f]{40}", record["baseline"]), "基线需完整提交号"
        dependency = re.search(r"^- 直接依赖：(.*)$", blocks[name], re.MULTILINE)
        assert dependency
        for dep in re.findall(r"\[([^]]+)\]\(#", dependency[1]):
            status = (
                data["gates"].get(dep, {}).get("status")
                if dep.startswith("G")
                else tasks.get(dep, {}).get("status")
            )
            assert status == ("PASSED" if dep.startswith("G") else "DONE"), (
                f"{name}依赖{dep}未通过"
            )
    # 门禁尚无自动放行：具名复核、适用A/F和真实证据必须另行审阅。
    for name, gate in data["gates"].items():
        assert name in blocks and name.startswith("G")
        assert gate["status"] in {"NOT_PASSED", "WAITING_REVIEW", "BLOCKED"}, (
            "门禁通过登记需扩展人工证据校验"
        )


def check(self_test: bool) -> dict:
    data = json.loads((EVIDENCE / "task-status.json").read_text(encoding="utf-8"))
    validate(data)
    rejected = 0
    if self_test:
        fixture = copy.deepcopy(data)
        fixture["tasks"]["R00-01"].update(
            status="DONE",
            technicalVerification={"result": "PASS"},
            dod=[{"item": i, "technical": "PASS"} for i in (1, 2, 3)],
            review={
                "backend": "PASS",
                "frontend": "PASS",
                "records": {
                    role: {
                        "name": "SELF_TEST_ONLY",
                        "date": "2026-10-27",
                        "baseline": "0" * 40,
                        "source": "synthetic-in-memory",
                    }
                    for role in ("backend", "frontend")
                },
            },
        )
        validate(fixture)  # 正例仅驻内存，绝不写入真实签字台账。
        for mutate in (
            lambda d: d.update(defaultTaskStatus="DONE"),
            lambda d: d["tasks"]["R00-01"].update(status="DONE"),
            lambda d: d["tasks"]["R00-01"].update(status="PASS"),
            lambda d: d["tasks"]["R00-01"].update(evidence="missing.md"),
            lambda d: d["tasks"]["R00-01"].update(evidence="../contracts/冻结决定.md"),
        ):
            bad = copy.deepcopy(data)
            mutate(bad)
            try:
                validate(bad)
            except (AssertionError, KeyError):
                rejected += 1
            else:
                raise AssertionError("非法台账未被拒绝")
        for mutate in (
            lambda d: d["tasks"]["R00-01"]["review"].update(frontend="PENDING"),
            lambda d: d["tasks"]["R00-01"]["review"].update(frontend="NOT_APPLICABLE"),
            lambda d: d["tasks"]["R00-01"]["review"]["records"]["backend"].pop(
                "source"
            ),
            lambda d: d["tasks"]["R00-01"].update(blockedBy=["E08"]),
            lambda d: d["tasks"]["R00-01"]["dod"][0].update(technical="NOT_RUN"),
            lambda d: d["tasks"]["R00-01"].update(dod=[]),
            lambda d: d["tasks"]["R00-01"]["technicalVerification"].update(
                result="FAIL"
            ),
        ):
            bad = copy.deepcopy(fixture)
            mutate(bad)
            try:
                validate(bad)
            except (AssertionError, KeyError):
                rejected += 1
            else:
                raise AssertionError("非法DONE未被拒绝")
        dependency_fixture = copy.deepcopy(fixture)
        dependency_fixture["tasks"]["R00-02"] = copy.deepcopy(
            fixture["tasks"]["R00-01"]
        )
        validate(dependency_fixture)
        dependency_fixture["tasks"]["R00-01"]["status"] = "WAITING_REVIEW"
        try:
            validate(dependency_fixture)
        except AssertionError:
            rejected += 1
        else:
            raise AssertionError("缺失DONE依赖未被拒绝")
    return {
        "result": "PASS",
        "registeredTasks": len(data["tasks"]),
        "invalidVariantsRejected": rejected,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    print(
        json.dumps(check(parser.parse_args().self_test), ensure_ascii=False, indent=2)
    )
