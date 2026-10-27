"""完整目标键与原子状态领取/fence。"""

from pathlib import Path

from alembic import op

revision = "0030_target_guards"
down_revision = "0029_budget_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("在途守卫不得删除，使用前向迁移维护")
