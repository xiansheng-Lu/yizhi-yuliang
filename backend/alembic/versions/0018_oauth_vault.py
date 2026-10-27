"""一次性OAuth state与带版本加密授权；不把授权当账户能力证据。"""

from pathlib import Path

from alembic import op

revision = "0018_oauth_vault"
down_revision = "0017_platform_payloads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留授权和换码事实，使用前向迁移维护")
