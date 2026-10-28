"""原生令牌续期的受限持久化端口。"""

from pathlib import Path

from alembic import op

revision = "0039_native_token_renewal"
down_revision = "0038_event_projections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("授权续期审计不可删除，采用前向迁移")
