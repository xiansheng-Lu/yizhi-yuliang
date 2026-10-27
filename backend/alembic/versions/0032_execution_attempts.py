"""执行单、SENT尝试、稳定平台键与一次性发送闸门。"""

from pathlib import Path

from alembic import op

revision = "0032_execution_attempts"
down_revision = "0031_api_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("执行与SENT尝试不可删除或覆盖，使用前向迁移维护")
