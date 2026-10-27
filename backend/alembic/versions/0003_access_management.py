"""M0-13：五角色种子、最小范围管理函数及专属追加审计。"""

from pathlib import Path

from alembic import op

revision = "0003_access_management"
down_revision = "0002_tenant_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQL文件含PL/pgSQL %ROWTYPE，明确无参数执行，避免驱动当成pyformat占位符。
    bind = op.get_bind().execution_options(no_parameters=True)
    bind.exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))
    bind.exec_driver_sql(
        (Path(__file__).parent / "0003_access_seeds.sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("权限和审计数据保留；回退应用，修复采用前向迁移")
