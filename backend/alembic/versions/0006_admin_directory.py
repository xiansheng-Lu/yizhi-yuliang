"""M0-14：独立管理鉴权与安全投影，不能接受任意表名或业务写操作。"""

from pathlib import Path

from alembic import op

revision = "0006_admin_directory"
down_revision = "0005_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("管理审计保留，默认关闭管理入口并以经过审阅的前向迁移修复")
