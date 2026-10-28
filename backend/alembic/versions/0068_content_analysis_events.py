"""Real analysis and workflow notifications, without command-job impersonation."""

from pathlib import Path

from alembic import op

revision = "0068_content_analysis_events"
down_revision = "0067_content_account_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("持久通知及提交序不可回退，使用前向迁移")
