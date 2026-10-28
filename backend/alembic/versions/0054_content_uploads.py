"""私有素材上传计划、对象版本与完成确认。"""

from pathlib import Path

from alembic import op

revision = "0054_content_uploads"
down_revision = "0053_analysis_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留素材与对象版本，通过停用入口回滚")
