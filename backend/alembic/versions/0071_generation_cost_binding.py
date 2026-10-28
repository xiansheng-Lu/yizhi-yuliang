"""生成任务绑定原费用许可；恢复不持久化或返回能力令牌。"""

from pathlib import Path

from alembic import op

revision = "0071_generation_cost_binding"
down_revision = "0070_generation_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留生成与费用历史，使用前向迁移")
