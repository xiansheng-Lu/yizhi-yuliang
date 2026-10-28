"""完整配置上下文字段依据与当前双范围读取。"""

from pathlib import Path

from alembic import op

revision = "0041_configuration_fields"
down_revision = "0040_connector_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留字段依据历史，采用前向迁移")
