"""M0-09/11：最小认证函数面，创建和精确授权同一事务。"""

from pathlib import Path

from alembic import op

revision = "0004_password_sessions"
down_revision = "0003_access_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留会话吊销与认证审计事实；应用回退后以经过复核的前向迁移修复")
