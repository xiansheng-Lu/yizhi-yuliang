"""批准派发worker准入事实；不开放生产serializer。"""

from pathlib import Path

from alembic import op

revision = "0077_approved_dispatch_worker"
down_revision = "0076_approval_execution_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("执行事实仅使用前向修复")
