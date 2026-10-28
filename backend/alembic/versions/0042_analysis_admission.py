"""私有分析受理、原键恢复与实际持久投递记录。"""

from pathlib import Path

from alembic import op

revision = "0042_analysis_admission"
down_revision = "0041_configuration_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留分析申请与原键恢复历史，采用前向迁移")
