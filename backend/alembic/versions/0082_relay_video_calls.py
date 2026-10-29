"""M3-19a 中转站视频调用记录：本地幂等键、冻结单价、真实产物与实测时长。"""

from pathlib import Path

from alembic import op

revision = "0082_relay_video_calls"
down_revision = "0081_video_generation_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("中转站视频调用记录不支持回滚")
