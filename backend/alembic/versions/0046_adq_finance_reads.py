"""ADQ资金观察、操作日志和独立钱包口径依据。"""

from pathlib import Path

from alembic import op

revision = "0046_adq_finance_reads"
down_revision = "0045_adq_asset_mirrors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留资金/日志观察，采用前向迁移")
