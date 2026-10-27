"""完整事实业务键、来源水位与追加修订；新表兼容扩展，回退保留事实。"""

from pathlib import Path

from alembic import op

revision = "0020_metric_facts"
down_revision = "0019_sync_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留规范事实与来源修订，使用前向迁移维护")
