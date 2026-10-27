"""带原始尝试/独立匹配依据的预算结算与释放。"""

from pathlib import Path

from alembic import op

revision = "0034_budget_settlement"
down_revision = "0033_execution_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("结算与释放账不可删除，采用前向迁移")
