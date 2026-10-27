"""业务会话只接受可信认证结果；每个事务首条语句绑定租户，提交/回滚自动清理。"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session

TENANT_SQL = text("SELECT set_config('app.current_tenant', :tenant_id, true)")


@dataclass(frozen=True)
class VerifiedIdentity:
    """只能由服务端重新验证会话后构造；HTTP请求体/游标不能实例化此对象。"""

    tenant_id: int
    membership_id: UUID
    user_id: UUID

    def __post_init__(self) -> None:
        if type(self.tenant_id) is not int or not 0 < self.tenant_id <= 9223372036854775807:
            raise ValueError("需要服务端验证的正bigint租户")


@contextmanager
def business_transaction(engine: Engine, identity: VerifiedIdentity) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session, session.begin():
        session.execute(TENANT_SQL, {"tenant_id": str(identity.tenant_id)})
        yield session


@asynccontextmanager
async def async_business_transaction(
    engine: AsyncEngine, identity: VerifiedIdentity
) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        await session.execute(TENANT_SQL, {"tenant_id": str(identity.tenant_id)})
        yield session
