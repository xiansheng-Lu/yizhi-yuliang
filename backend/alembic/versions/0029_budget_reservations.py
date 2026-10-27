"""受控正增额预占与同桶串行裁断。"""

from pathlib import Path

from alembic import op

revision = "0029_budget_reservations"
down_revision = "0028_budget_initialization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("未结算预占不得删除，使用前向迁移维护")
