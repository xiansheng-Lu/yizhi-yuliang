"""精确任务的审批人当前资格布尔检查；不授予users读取。"""

from pathlib import Path

from alembic import op

revision = "0027_intent_qualification"
down_revision = "0026_rule_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留冻结意图与核验接口，使用前向迁移维护")
