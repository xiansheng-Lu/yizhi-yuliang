"""同步批次、确认游标与分页原子镜像；不改变真实平台能力。"""

from pathlib import Path

from alembic import op

revision = "0019_sync_checkpoints"
down_revision = "0018_oauth_vault"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留同步确认事实，使用前向迁移维护")
