"""M5-02a：报价确认、预算预占与一次性调用许可；未知结果保留预占，不删账本回退。"""

from pathlib import Path

from alembic import op

revision = "0012_ai_cost_gate"
down_revision = "0011_email_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("费用许可/预占/回执为核对事实，停止新调用并以前向维护处理")
