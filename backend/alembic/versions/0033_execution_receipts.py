"""持久响应回执、版本顺序与旧fence隔离。"""

from pathlib import Path

from alembic import op

revision = "0033_execution_receipts"
down_revision = "0032_execution_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("回执与隔离证据不可删除；采用前向迁移")
