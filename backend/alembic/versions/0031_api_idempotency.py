"""数据库事务操作的持久接口幂等与首次结果。"""

from pathlib import Path

from alembic import op

revision = "0031_api_idempotency"
down_revision = "0030_target_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("幂等键与首次结果不可删除后重用，使用前向迁移维护")
