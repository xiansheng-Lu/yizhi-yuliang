"""固定报告发布版本及仅凭能力摘要读取的公开入口。"""

from pathlib import Path

from alembic import op

revision = "0061_report_publications"
down_revision = "0060_frontend_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("已发布版本保留历史证据，采用前向迁移")
