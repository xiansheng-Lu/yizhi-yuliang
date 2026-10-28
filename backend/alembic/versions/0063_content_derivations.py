"""派生键、父版本与不可变子文档首版关联。"""

from pathlib import Path

from alembic import op

revision = "0063_content_derivations"
down_revision = "0062_plan_read_projections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留派生历史及原请求键，采用前向迁移")
