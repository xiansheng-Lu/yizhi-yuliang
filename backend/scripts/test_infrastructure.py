"""显式本地设施验证入口，凭据仅传子进程环境，不打印连接串。"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lifecycle", action="store_true", help="包含本项目服务重启与持久化验证")
    parser.add_argument("--details", action="store_true", help="输出已遮蔽本地凭据的短失败诊断")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
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
        YULIANG_TEST_REDIS_URL=(f"redis://:{values['YULIANG_REDIS_PASSWORD']}@127.0.0.1:16379/0"),
        YULIANG_TEST_BROKER_URL=(
            f"amqp://yuliang:{values['YULIANG_BROKER_PASSWORD']}@127.0.0.1:15672/yuliang"
        ),
        YULIANG_TEST_S3_URL="http://127.0.0.1:19000",
        YULIANG_TEST_S3_SECRET=values["YULIANG_MINIO_PASSWORD"],
        YULIANG_TEST_LIFECYCLE="1" if args.lifecycle else "0",
    )
    # --tb=no避免第三方库在失败栈中展开凭据；失败仍保留测试名与状态。
    result = subprocess.run(  # noqa: S603 -- 固定测试入口，禁止任意命令。
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration",
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
    for value in values.values():
        output = output.replace(value, "[REDACTED]")
    print(output, end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
