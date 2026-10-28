"""ADQ原生只读资产与配置变化；不赋予计划店铺关系或执行权限。"""

from pathlib import Path

from alembic import op

revision = "0045_adq_asset_mirrors"
down_revision = "0044_read_route_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留原始只读资产与修订依据，采用前向迁移")
