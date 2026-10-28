"""本地稿固定清单与逐条原子导入断点。"""
from pathlib import Path

from alembic import op

revision = "0065_content_imports"
down_revision = "0064_workflow_templates_runs"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))

def downgrade() -> None:
    raise RuntimeError("导入映射证据采用前向迁移")
