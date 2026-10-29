"""通知模块 M2-05b：notifications 表脚手架。

只建表，不绑定渠道实现；后续 M2-20 / M2-21 接分发。
"""

from pathlib import Path

from alembic import op

revision = "0080_notifications"
down_revision = "0079_execution_timeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("通知脚手架表不支持回滚")
