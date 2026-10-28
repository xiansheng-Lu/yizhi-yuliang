"""内容AI请求、MODEL来源及调用复合关联。"""

from pathlib import Path

from alembic import op

revision = "0069_ai_content_requests"
down_revision = "0068_content_analysis_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留AI内容调用与候选审计，采用前向迁移")
