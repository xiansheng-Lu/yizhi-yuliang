"""规则读取的预算和E2事实依赖；初始化/执行生产者另卡接入。"""

from pathlib import Path

from alembic import op

revision = "0026_rule_facts"
down_revision = "0025_risk_framework"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留预算与核验事实，使用前向迁移维护")
