"""真实资源来源上下文与不可变分页。"""

from pathlib import Path

from alembic import op

revision = "0052_resource_lookups"
down_revision = "0051_cloud_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留资源分页来源；前向迁移")
