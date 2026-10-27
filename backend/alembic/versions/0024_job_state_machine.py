"""任务状态条件更新与不可变事件版本；不装配实际执行参与者。"""

from pathlib import Path

from alembic import op

revision = "0024_job_state_machine"
down_revision = "0023_job_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("状态事件和领取标记不能丢弃，使用前向迁移维护")
