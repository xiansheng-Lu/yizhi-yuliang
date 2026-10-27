"""完整申请与旧任务标识并存，不为旧占位记录捏造风险或现值。"""

from pathlib import Path

from alembic import op

revision = "0023_job_applications"
down_revision = "0022_connection_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("申请与历史不可删除，使用前向迁移维护")
