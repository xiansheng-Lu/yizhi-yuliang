"""账户动作的不可混用证据与单次灰度许可。"""

from pathlib import Path

from alembic import op

revision = "0016_capability_evidence"
down_revision = "0015_platform_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留能力与许可事实，使用前向迁移维护")
