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
    """从 YULIANG_ 环境变量 / .env 读取；不传 _env_file=None 以允许 .env 生效。"""
    import os

    # 允许通过环境变量覆盖；若未设置则使用 .env 中的值（Settings 默认行为）
    os.environ.setdefault("YULIANG_ENVIRONMENT", "test")
    os.environ.setdefault(
        "YULIANG_SESSION_SECRET", "test-only-secret-with-at-least-32-characters"
    )
    return Settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value
