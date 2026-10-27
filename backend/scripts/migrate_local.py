"""本地WSL迁移窗口：独立角色临时LOGIN，完成/失败均关闭；不删除业务数据。"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import psycopg


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("upgrade", "current"), default="upgrade", nargs="?")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    values = dict(
        line.split("=", 1)
        for line in (root / "deployment/.env.infrastructure")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    )
    admin = f"postgresql://postgres:{values['YULIANG_POSTGRES_PASSWORD']}@127.0.0.1:15432/yizhi"
    migration = (
        f"postgresql+psycopg://app_migrate:{values['YULIANG_MIGRATION_PASSWORD']}"
        "@127.0.0.1:15432/yizhi"
    )
    env = os.environ.copy()
    env["YULIANG_MIGRATION_DATABASE_URL"] = migration
    with psycopg.connect(admin, autocommit=True) as control:
        if control.execute("SELECT pg_try_advisory_lock(760001)").fetchone() != (True,):
            raise SystemExit("已有本地迁移窗口，拒绝并发修改")
        if control.execute(
            "SELECT rolcanlogin FROM pg_roles WHERE rolname='app_migrate'"
        ).fetchone() != (False,):
            raise SystemExit("迁移角色不是默认NOLOGIN，请先确认已有维护窗口")
        control.execute("ALTER ROLE app_migrate LOGIN")
        try:
            command = ["upgrade", "head"] if args.action == "upgrade" else ["current"]
            result = subprocess.run(  # noqa: S603 -- 固定本地Alembic入口。
                [sys.executable, "-m", "alembic", "-c", str(root / "alembic.ini"), *command],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        finally:
            control.execute("ALTER ROLE app_migrate NOLOGIN")
        output = result.stdout + result.stderr
        for value in values.values():
            output = output.replace(value, "[REDACTED]")
        print(output.strip() or ("迁移完成" if result.returncode == 0 else "迁移失败"))
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
