"""完整含暂停计划来源驱动预算B初始化与来源修订。"""

from pathlib import Path

from alembic import op

revision = "0028_budget_initialization"
down_revision = "0027_intent_qualification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留预算观察与差异，使用前向迁移维护")
