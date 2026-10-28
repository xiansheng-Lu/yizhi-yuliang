"""本人任务的当前授权只读执行证据，不修改原执行私有RLS。"""

from pathlib import Path

from alembic import op

revision = "0075_execution_evidence_reads"
down_revision = "0074_resource_query_cursors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION access.execution_evidence_read(uuid,uuid,uuid)")
