"""M2-02：本人项目/会话/消息、真实任务标识FK与归类；兼容扩展，不开放执行。"""

from pathlib import Path

from alembic import op

revision = "0010_workspace_history"
down_revision = "0009_ai_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留对话与任务关联事实，回退应用并以前向维护修复")
