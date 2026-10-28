"""生成账本及先持久发送栅栏。"""

from pathlib import Path

from alembic import op

revision = "0070_generation_jobs"
down_revision = "0069_ai_content_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("生成账本采用前向迁移")
