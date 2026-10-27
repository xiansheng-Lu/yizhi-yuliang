"""私有解析候选留痕，不增加平台执行能力。"""

from pathlib import Path

from alembic import op

revision = "0014_command_interpretations"
down_revision = "0013_ai_response_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("解析留痕为核对事实，保留并以前向迁移维护")
