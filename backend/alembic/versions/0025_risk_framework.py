"""不可变策略版本与关联申请的四时点评估记录。"""

from pathlib import Path

from alembic import op

revision = "0025_risk_framework"
down_revision = "0024_job_state_machine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留历史策略和风险依据，使用前向迁移维护")
