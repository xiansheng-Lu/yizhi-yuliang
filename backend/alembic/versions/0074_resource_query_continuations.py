"""ADGROUP私有分页定位元数据，不赋任何业务权限。"""

from pathlib import Path

from alembic import op

revision = "0074_resource_query_cursors"
down_revision = "0073_content_topic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("前向迁移；分页定位可到期清理，不回滚业务来源")
