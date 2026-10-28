"""模型账本受控入口绑定实际事务actor，保留已有参数与结果合同。"""

from pathlib import Path

from alembic import op

revision = "0072_ai_ledger_actor_guard"
down_revision = "0071_generation_cost_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("账本actor边界采用前向迁移")
