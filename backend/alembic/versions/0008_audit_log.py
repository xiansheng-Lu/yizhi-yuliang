"""M0-17：追加分区审计，兼容旧应用；前向扩展，无运行角色DDL或删表回退。"""

from pathlib import Path

from alembic import op

revision = "0008_audit_log"
down_revision = "0007_reliable_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留不可改写审计事实；回退应用并以前向维护修复")
