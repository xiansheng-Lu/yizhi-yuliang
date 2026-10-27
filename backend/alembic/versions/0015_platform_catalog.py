"""版本化描述性平台目录；运行账号只能读取，不能改目录或启用路由。"""

from pathlib import Path

from alembic import op

revision = "0015_platform_catalog"
down_revision = "0014_command_interpretations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    )


def downgrade() -> None:
    raise RuntimeError("保留来源与导入历史，使用前向迁移维护")
