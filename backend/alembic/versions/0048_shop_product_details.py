"""小店商品详情观察及单位/状态独立依据。"""

from pathlib import Path

from alembic import op

revision = "0048_shop_product_details"
down_revision = "0047_adq_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留商品历史观察，使用前向停用")
