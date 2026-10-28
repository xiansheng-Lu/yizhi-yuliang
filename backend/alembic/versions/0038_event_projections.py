"""原子业务事件与租户提交序投影。"""

from pathlib import Path

from alembic import op

revision = "0038_event_projections"
down_revision = "0037_approval_service"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留事件去重与水位证据，采用前向迁移")
