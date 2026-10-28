"""资源与能力逐项映射依据，禁止按URL后缀猜测资源或写能力。"""

from pathlib import Path

from alembic import op

revision = "0043_integration_operations"
down_revision = "0042_analysis_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留资源操作映射依据，采用前向迁移")
