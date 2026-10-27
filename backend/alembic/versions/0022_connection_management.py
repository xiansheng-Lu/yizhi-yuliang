"""连接影响面、受控本地断开和账户授权关联；保留历史。"""

from pathlib import Path

from alembic import op

revision = "0022_connection_management"
down_revision = "0021_resource_freshness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留断开记录与历史，使用前向迁移维护")
