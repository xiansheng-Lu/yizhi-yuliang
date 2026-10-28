"""实际只读报表批次、日水位、来源契约与规范事实缺口。"""

from pathlib import Path

from alembic import op

revision = "0047_adq_reports"
down_revision = "0046_adq_finance_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留报表观察与校准依据，采用前向迁移")
