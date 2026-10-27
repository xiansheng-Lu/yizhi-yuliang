"""外部预算差异、告警与证据解冻。"""

from pathlib import Path

from alembic import op

revision = "0035_budget_governance"
down_revision = "0034_budget_settlement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("预算差异与告警保留，采用前向迁移")
