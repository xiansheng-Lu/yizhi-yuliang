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
    """从 YULIANG_ 环境变量 / .env 读取真实 DB/Redis/Rabbit/MinIO 端口和凭据。

    同时显式关闭平台资源、身份接口等开关——测试在隔离环境下运行，
    不接真实平台配置，由各 integration 测试按需要自行启用。
    """
    import os

    os.environ.setdefault("YULIANG_ENVIRONMENT", "test")
    os.environ.setdefault(
        "YULIANG_SESSION_SECRET", "test-only-secret-with-at-least-32-characters"
    )
    # _env_file=None 阻止读取项目 .env（其中 YULIANG_PLATFORM_RESOURCES_FILE 会触发
    # Settings 的平台校验器），改为只让 Settings 从显式传入的值和 YULIANG_ 环境变量构造。
    # DB/Redis/Rabbit 端口凭据由 pytest fixture 显式传入，不依赖 .env。
    return Settings(
        _env_file=None,
        platform_resources_file=None,
        identity_api_enabled=False,
        email_login_enabled=False,
        admin_api_enabled=False,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value
