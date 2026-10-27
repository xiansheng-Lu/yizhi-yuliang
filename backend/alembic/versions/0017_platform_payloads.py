"""平台请求与脱敏响应的追加存档，不创建执行成功事实。"""

from pathlib import Path

from alembic import op

revision = "0017_platform_payloads"
down_revision = "0016_capability_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留请求与响应核对事实，使用前向迁移维护")
