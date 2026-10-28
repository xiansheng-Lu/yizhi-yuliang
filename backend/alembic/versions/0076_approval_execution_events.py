"""真实批准与原申请者执行意图同事务；不开放生产发送。"""

from pathlib import Path

from alembic import op

revision = "0076_approval_execution_events"
down_revision = "0075_execution_evidence_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留批准事件来源及可靠投递事实，使用前向迁移")
