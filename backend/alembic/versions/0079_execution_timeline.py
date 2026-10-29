"""原actor安全结构化时间线，只读，不修改旧前端历史。"""

from pathlib import Path

from alembic import op

revision = "0079_execution_timeline"
down_revision = "0078_execution_manual_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("时间线只读出口使用前向修复")
