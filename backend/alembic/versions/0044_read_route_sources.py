"""固定SDK只读路径的独立来源，不修改描述目录或自动生成能力证据。"""

from pathlib import Path

from alembic import op

revision = "0044_read_route_sources"
down_revision = "0043_integration_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留独立路径依据，采用前向迁移")
