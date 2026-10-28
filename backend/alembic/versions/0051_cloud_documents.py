"""云文档和在途引用。"""

from pathlib import Path

from alembic import op

revision = "0051_cloud_documents"
down_revision = "0050_shop_orders_settlement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留云稿历史，使用前向软删")
