"""连接器真实变更、证据到期队列及范围事件。"""

from pathlib import Path

from alembic import op

revision = "0040_connector_events"
down_revision = "0039_native_token_renewal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留通知修订与截止证据，采用前向迁移")
