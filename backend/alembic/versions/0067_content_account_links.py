"""素材账号关联意图与独立历史，不冒充平台上传回执。"""

from pathlib import Path

from alembic import op

revision = "0067_content_account_links"
down_revision = "0066_workflow_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("账号意图历史采用前向迁移")
