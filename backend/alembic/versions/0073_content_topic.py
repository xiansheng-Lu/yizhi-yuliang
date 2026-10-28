"""新增独立内容选题场景；旧三场景/已持久请求保持不变。"""

from pathlib import Path

from alembic import op

revision = "0073_content_topic"
down_revision = "0072_ai_ledger_actor_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留新增选题调用与提示来源，采用前向迁移")
