"""不可变审批载荷、真实决定与后台策略白名单。"""

from pathlib import Path

from alembic import op

revision = "0037_approval_service"
down_revision = "0036_execution_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("审批和决定证据不可删除，采用前向迁移")
