"""M3-19a 视频生成编排账本：按秒/按次价源语义、费用启动兼容与任务状态机。"""

from pathlib import Path

from alembic import op

revision = "0081_video_generation_tasks"
down_revision = "0080_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("视频生成任务账本不支持回滚")
