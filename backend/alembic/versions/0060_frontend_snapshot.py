"""当前成员工作区scope及内容变化revision；不存平台秘密或授予数据权限。"""

from pathlib import Path

from alembic import op

revision = "0060_frontend_snapshot"
down_revision = "0059_content_asset_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留成员scope与版本历史，采用前向迁移")
