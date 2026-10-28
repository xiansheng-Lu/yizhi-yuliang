"""推广资产来源与人群来源的精确上下文按kind隔离。"""

from pathlib import Path

from alembic import op

revision = "0056_lookup_target_assets"
down_revision = "0055_analysis_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留资源来源依据，采用前向迁移")
