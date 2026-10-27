"""资源时钟、受控写前置契约与安全同步观察摘要；仅兼容扩展。"""

from pathlib import Path

from alembic import op

revision = "0021_resource_freshness"
down_revision = "0020_metric_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留资源时钟与前置契约，使用前向迁移维护")
