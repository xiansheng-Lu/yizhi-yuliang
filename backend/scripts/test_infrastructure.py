"""显式本地设施验证入口，凭据仅传子进程环境，不打印连接串。"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def infrastructure_environment(
    root: Path, lifecycle: bool = False
) -> tuple[dict[str, str], list[str]]:
    values = dict(
        line.split("=", 1)
        for line in (root / "deployment/.env.infrastructure")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    )
    env = os.environ.copy()
    env.update(
        YULIANG_TEST_DATABASE_URL=(
            f"postgresql://app_login:{values['YULIANG_APP_PASSWORD']}@127.0.0.1:15432/yizhi"
        ),
        YULIANG_TEST_ADMIN_DATABASE_URL=(
            f"postgresql://postgres:{values['YULIANG_POSTGRES_PASSWORD']}@127.0.0.1:15432/yizhi"
        ),
        YULIANG_TEST_MIGRATION_DATABASE_URL=(
            f"postgresql://app_migrate:{values['YULIANG_MIGRATION_PASSWORD']}@127.0.0.1:15432/yizhi"
        ),
        YULIANG_TEST_AUTH_DATABASE_URL=(
            f"postgresql://app_auth:{values['YULIANG_AUTH_PASSWORD']}@127.0.0.1:15432/yizhi"
        ),
        YULIANG_TEST_REDIS_URL=(f"redis://:{values['YULIANG_REDIS_PASSWORD']}@127.0.0.1:16379/0"),
        YULIANG_TEST_BROKER_URL=(
            f"amqp://yuliang:{values['YULIANG_BROKER_PASSWORD']}@127.0.0.1:15672/yuliang"
        ),
        YULIANG_TEST_S3_URL="http://127.0.0.1:19000",
        YULIANG_TEST_S3_SECRET=values["YULIANG_MINIO_PASSWORD"],
        YULIANG_TEST_LIFECYCLE="1" if lifecycle else "0",
    )
    return env, list(values.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lifecycle", action="store_true", help="包含本项目服务重启与持久化验证")
    parser.add_argument("--details", action="store_true", help="输出已遮蔽本地凭据的短失败诊断")
    parser.add_argument(
        "--group",
        choices=(
            "all",
            "messages",
            "worker",
            "identity",
            "mail",
            "cost",
            "gateway",
            "parser",
            "catalog",
            "capability",
            "payload",
            "governance",
            "oauth",
            "sync",
            "facts",
            "freshness",
        ),
        default="all",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    lifecycle = "tests/integration/test_infrastructure_lifecycle.py"
    paths = {
        # 重启演练先运行，避免后续Worker复用在演练前建立的进程级MQ连接池。
        "all": [lifecycle]
        + [
            str(path.relative_to(root))
            for path in sorted((root / "tests/integration").glob("test_*.py"))
            if path.name != "test_infrastructure_lifecycle.py"
        ],
        "messages": ["tests/integration/test_durable_messaging.py"],
        "worker": ["tests/integration/test_worker.py"],
        "mail": ["tests/integration/test_email_codes.py"],
        "cost": ["tests/integration/test_ai_cost_gate.py"],
        "gateway": ["tests/integration/test_deepseek_gateway.py"],
        "catalog": ["tests/integration/test_platform_catalog.py"],
        "capability": ["tests/integration/test_capability_evidence.py"],
        "payload": ["tests/integration/test_platform_payloads.py"],
        "governance": ["tests/integration/test_platform_governance.py"],
        "oauth": ["tests/integration/test_oauth_vault.py"],
        "sync": ["tests/integration/test_sync_checkpoints.py"],
        "facts": ["tests/integration/test_metric_facts.py"],
        "freshness": ["tests/integration/test_resource_freshness.py"],
        "parser": [
            "tests/integration/test_command_parse.py",
            "tests/integration/test_workspace.py",
            "tests/integration/test_deepseek_gateway.py",
        ],
        "identity": [
            "tests/integration/test_identity_database.py",
            "tests/integration/test_password_sessions.py",
        ],
    }[args.group]
    env, secret_values = infrastructure_environment(root, args.lifecycle)
    # --tb=no避免第三方库在失败栈中展开凭据；失败仍保留测试名与状态。
    result = subprocess.run(  # noqa: S603 -- 固定测试入口，禁止任意命令。
        [
            sys.executable,
            "-m",
            "pytest",
            *paths,
            "-m",
            "integration",
            "-q",
            "--tb=short" if args.details else "--tb=no",
            "-rN",
            "--show-capture=no",
        ],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    for value in secret_values:
        output = output.replace(value, "[REDACTED]")
    print(output, end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
