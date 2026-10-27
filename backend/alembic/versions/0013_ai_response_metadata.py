"""模型响应标识与摘要追加到同一次调用完成事务；无正文/密钥。"""

from pathlib import Path

from alembic import op

revision = "0013_ai_response_metadata"
down_revision = "0012_ai_cost_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("响应标识为核对事实，保留并以前向迁移维护")
