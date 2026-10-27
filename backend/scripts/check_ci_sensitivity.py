"""隔离副本中故意破坏契约/迁移/隔离，必须被实际测试拒绝；不修改工作区或开发库。"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from test_infrastructure import infrastructure_environment

CASES = (
    (
        "contract",
        "src/yuliang/platform/envelope.py",
        'Literal["command-job-v1.0"] = "command-job-v1.0"',
        'Literal["broken-version"] = "broken-version"',
        "tests/contract/test_envelope.py::test_success_200_202_and_null_keep_outer_version",
        "failed",
    ),
    (
        "migration",
        "alembic/versions/0008_audit_log.sql",
        "CREATE TABLE audit_logs (",
        "CREATE TABL audit_logs (",
        "tests/integration/test_identity_database.py::test_m0_06_empty_upgrade_repeat_and_safe_rollback",
        "error",
    ),
    (
        "isolation",
        "alembic/versions/0002_tenant_policies.py",
        'predicate = "tenant_id = public.current_tenant_id()"',
        'predicate = "true"',
        "tests/integration/test_identity_database.py::test_m0_07_pool_reuse_and_rollback",
        "failed",
    ),
)


def check(root: Path) -> list[dict[str, object]]:
    env, _ = infrastructure_environment(root)
    reports: list[dict[str, object]] = []
    for name, relative, before, after, node, failure in CASES:
        with tempfile.TemporaryDirectory(prefix="yuliang-ci-mutation-") as temporary:
            target = Path(temporary) / "backend"
            target.mkdir()
            for folder in ("src", "tests", "alembic"):
                shutil.copytree(
                    root / folder,
                    target / folder,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
                )
            for file in ("pyproject.toml", "alembic.ini"):
                shutil.copyfile(root / file, target / file)
            source = target / relative
            original = source.read_text(encoding="utf-8")
            if original.count(before) != 1:
                raise RuntimeError("故障注入锚点变化，需复核敏感性场景：" + name)
            process_env = env.copy()
            process_env["PYTHONPATH"] = str(target / "src")
            command = [
                sys.executable,
                "-m",
                "pytest",
                node,
                "-q",
                "--tb=short",
                "-rA",
                "--show-capture=no",
            ]
            baseline = subprocess.run(  # noqa: S603 -- 同一固定节点先确认健康基线。
                command,
                cwd=target,
                env=process_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if baseline.returncode != 0:
                raise RuntimeError("故障注入前基线未通过：" + name)
            source.write_text(original.replace(before, after, 1), encoding="utf-8")
            result = subprocess.run(  # noqa: S603 -- 固定测试节点，副本与测试库均唯一命名。
                command,
                cwd=target,
                env=process_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            output = result.stdout + result.stderr
            rejected = (
                result.returncode == 1
                and re.search(r"\b1 " + failure + r"\b", output) is not None
                and node.rsplit("::", 1)[1] in output
            )
            diagnostic = {
                "contract": "broken-version",
                "migration": "TABL",
                "isolation": "visible_shops(session)",
            }[name] in output
            rejected = rejected and diagnostic
            reports.append(
                {
                    "case": name,
                    "mutatedFile": relative,
                    "test": node,
                    "baselineExitCode": baseline.returncode,
                    "exitCode": result.returncode,
                    "matchedDiagnostic": diagnostic,
                    "expectedOutcome": failure,
                    "rejected": rejected,
                }
            )
            if not rejected:
                # 不回显第三方异常、环境变量或捕获载荷；保持失败，不误记PASS。
                raise RuntimeError("CI故障敏感性未通过：" + name)
    return reports


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    report = {"result": "PASS", "cases": check(root), "livePlatformCalls": "NOT_RUN"}
    archive = root.parent / ".runtime/ci-sensitivity.json"
    archive.parent.mkdir(exist_ok=True)
    archive.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
