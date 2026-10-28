"""独立罗盘日观察。"""

from pathlib import Path

from alembic import op

revision = "0049_shop_compass"
down_revision = "0048_shop_product_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留经营来源观察；使用前向迁移")
