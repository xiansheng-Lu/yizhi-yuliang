"""本地WSL迁移窗口：独立角色临时LOGIN，完成/失败均关闭；不删除业务数据。"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import psycopg


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("upgrade", "current", "audit-partitions"), default="upgrade", nargs="?"
    )
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
            if args.action == "audit-partitions":
                with psycopg.connect(
                    migration.replace("postgresql+psycopg://", "postgresql://")
                ) as maintenance:
                    maintenance.execute("SET LOCAL ROLE app_owner")
                    maintenance.execute(
                        "SELECT access.ensure_audit_partition("
                        "date_trunc('month',now() AT TIME ZONE 'UTC')::date)"
                    )
                    maintenance.execute(
                        "SELECT access.ensure_audit_partition(("
                        "date_trunc('month',now() AT TIME ZONE 'UTC')+interval '1 month')::date)"
                    )
                print("当月与下月UTC审计分区就绪；迁移窗口关闭")
                return 0
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
