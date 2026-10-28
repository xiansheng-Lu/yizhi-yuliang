"""SOP模板版本与创作线，保留真实租户和商品绑定。"""

from pathlib import Path

from alembic import op

revision = "0064_workflow_templates_runs"
down_revision = "0063_content_derivations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留模板与创作线历史，采用前向迁移")
