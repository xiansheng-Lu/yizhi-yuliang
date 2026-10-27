"""测试分层在收集时生效；没有真实设施不能以模拟替代integration。"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yuliang.main import create_app
from yuliang.settings import Settings


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        for layer in ("unit", "integration", "contract", "live"):
            if layer in item.path.parts:
                item.add_marker(getattr(pytest.mark, layer))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://app_login:example@localhost/yizhi",
        auth_database_url="postgresql+psycopg://app_auth:example@localhost/yizhi",
        redis_url="redis://localhost:6379/0",
        broker_url="amqp://app:example@localhost/yizhi",
        session_secret="test-only-secret-with-at-least-32-characters",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value
