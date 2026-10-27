"""M0-10：预认证绑定的邮件码、原子消费建会话和投递状态；只扩展，不删认证事实。"""

from pathlib import Path

from alembic import op

revision = "0011_email_codes"
down_revision = "0010_workspace_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留邮件挑战/消费/认证审计事实，回退应用后以前向维护修复")
