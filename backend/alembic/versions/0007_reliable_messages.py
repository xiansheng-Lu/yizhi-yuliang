"""M0-16：租户Outbox/调度与受限系统Inbox。"""

from pathlib import Path

from alembic import op

revision = "0007_reliable_messages"
down_revision = "0006_admin_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("Outbox/Inbox为恢复事实，不自动删除；停止派发并使用前向修复")
