"""发送前持久恢复、只读重投及稳定未发送E1。"""

from pathlib import Path

from alembic import op

revision = "0036_execution_recovery"
down_revision = "0035_budget_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("恢复/未发送证据与在途账不可删除，采用前向迁移")
