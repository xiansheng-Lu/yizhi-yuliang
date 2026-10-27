"""迁移连接只从独立环境读取；不接受应用.env中的运行凭据。"""

import os

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from alembic import context

if context.is_offline_mode():
    raise RuntimeError("需要独立迁移连接进行在线事务验证；不生成未经验证的离线DDL")

url = os.environ.get("YULIANG_MIGRATION_DATABASE_URL")
if not url:
    raise RuntimeError("缺少独立YULIANG_MIGRATION_DATABASE_URL，拒绝使用应用身份迁移")

engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
try:
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET LOCAL ROLE app_owner")
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
