"""追加式人工陈述与原执行事实严格隔离。"""

from pathlib import Path

from alembic import op

revision = "0078_execution_manual_notes"
down_revision = "0077_approved_dispatch_worker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留人工陈述与审计历史，使用前向迁移")
