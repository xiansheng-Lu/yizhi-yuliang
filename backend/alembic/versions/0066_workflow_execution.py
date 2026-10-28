"""SOP人工候选、动作幂等与不可变历史。"""

from pathlib import Path

from alembic import op

revision = "0066_workflow_execution"
down_revision = "0065_content_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留SOP动作及候选历史，采用前向迁移")
