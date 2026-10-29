"""测试分层在收集时生效；没有真实设施不能以模拟替代integration。"""

import asyncio
import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path

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


def load_facility_environment() -> None:
    """未显式注入 YULIANG_TEST_* 时，复用设施脚本的映射读取 deployment/.env.infrastructure。

    这样 `uv run pytest` 可直接运行；文件缺失时保持原行为（缺少真实设施的用例
    明确失败，不以Mock替代）。映射只保留在 scripts/test_infrastructure.py 一处。
    """
    if os.environ.get("YULIANG_TEST_DATABASE_URL"):
        return
    root = Path(__file__).resolve().parents[1]
    if not (root / "deployment/.env.infrastructure").exists():
        return
    spec = importlib.util.spec_from_file_location(
        "yuliang_facility_environment", root / "scripts/test_infrastructure.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env, _ = module.infrastructure_environment(root)
    for key, value in env.items():
        if key.startswith("YULIANG_TEST_"):
            os.environ.setdefault(key, value)


def selector_policy() -> asyncio.AbstractEventLoopPolicy:
    """Windows默认ProactorEventLoop无法承载psycopg异步连接，统一改用Selector。

    在conftest导入时设置全局策略；pytest-asyncio在未指定loop_factory时按当前策略
    新建循环，TestClient/anyio直接创建的循环也受益。
    """
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


load_facility_environment()
if sys.platform == "win32":
    asyncio.set_event_loop_policy(selector_policy())


@pytest.fixture(scope="session", autouse=True)
def apply_selector_event_loop_policy() -> None:
    """其他插件/夹具在session内改回默认策略时，重新归一化一次。"""
    asyncio.set_event_loop_policy(selector_policy())


@pytest.fixture
def settings() -> Settings:
    """从 YULIANG_ 环境变量 / .env 读取真实 DB/Redis/Rabbit/MinIO 端口和凭据。

    同时显式关闭平台资源、身份接口等开关——测试在隔离环境下运行，
    不接真实平台配置，由各 integration 测试按需要自行启用。
    """
    os.environ.setdefault("YULIANG_ENVIRONMENT", "test")
    os.environ.setdefault(
        "YULIANG_SESSION_SECRET", "test-only-secret-with-at-least-32-characters"
    )
    # _env_file=None 阻止读取项目 .env（其中 YULIANG_PLATFORM_RESOURCES_FILE 会触发
    # Settings 的平台校验器），改为只让 Settings 从显式传入的值和 YULIANG_ 环境变量构造。
    # DB/Redis/Rabbit 的真实端口凭据由本地设施入口注入 YULIANG_TEST_*，这里显式转成
    # SQLAlchemy地址传入；缺少设施变量时仍允许由 YULIANG_* 环境变量提供（unit/contract）。
    explicit: dict[str, str] = {}
    for key, source in (
        ("database_url", "YULIANG_TEST_DATABASE_URL"),
        ("auth_database_url", "YULIANG_TEST_AUTH_DATABASE_URL"),
        ("redis_url", "YULIANG_TEST_REDIS_URL"),
        ("broker_url", "YULIANG_TEST_BROKER_URL"),
    ):
        if value := os.environ.get(source):
            explicit[key] = value.replace("postgresql://", "postgresql+psycopg://", 1)
    return Settings(
        _env_file=None,
        platform_resources_file=None,
        identity_api_enabled=False,
        email_login_enabled=False,
        admin_api_enabled=False,
        **explicit,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value
