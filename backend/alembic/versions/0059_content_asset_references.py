"""素材引用、历史版本与逻辑归档保护。"""

from pathlib import Path

from alembic import op

revision = "0059_content_asset_references"
down_revision = "0058_analysis_comparisons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("素材历史引用不可破坏性删除，使用前向停用")
