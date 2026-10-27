"""M2-01a：模型配置、提示词版本与调用事实；扩展兼容，未知费用不默认零。"""

from pathlib import Path

from alembic import op

revision = "0009_ai_ledger"
down_revision = "0008_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留调用与成本事实，回退应用并以前向修复维护")
