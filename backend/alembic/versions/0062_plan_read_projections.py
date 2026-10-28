"""计划完整分页观察与独立口径依据。"""
from pathlib import Path
from alembic import op
revision = "0062_plan_read_projections"
down_revision = "0061_report_publications"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))

def downgrade() -> None:
    raise RuntimeError("计划观察证据采用前向迁移")
