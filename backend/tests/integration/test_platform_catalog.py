"""真实PG验证整批原子性、并发幂等、目录隔离与运行角色不可提权。"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.catalog import connector_catalog, import_catalog, load_catalog
from yuliang.platform.db import VerifiedIdentity, business_transaction

from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def maintenance(database: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(
        database["migration"].replace("postgresql://", "postgresql+psycopg://"),
        hide_parameters=True,
    )
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE app_owner"))
        for statement in (
            "DELETE FROM catalog_head",
            "DELETE FROM endpoint_catalog",
            "DELETE FROM catalog_imports",
        ):
            connection.execute(text(statement))
    try:
        yield engine
    finally:
        engine.dispose()


def test_import_is_atomic_on_transaction_failure(maintenance: Engine) -> None:
    bundle = load_catalog(ROOT)
    with pytest.raises(RuntimeError, match="injected"):
        with maintenance.begin() as connection:
            connection.execute(text("SET LOCAL ROLE app_owner"))
            import_catalog(connection, bundle)
            raise RuntimeError("injected after rows before commit")
    with maintenance.begin() as connection:
        connection.execute(text("SET LOCAL ROLE app_owner"))
        assert connection.execute(text("SELECT count(*) FROM endpoint_catalog")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM catalog_imports")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM catalog_head")).scalar_one() == 0


def test_parallel_import_reuses_one_complete_version(maintenance: Engine) -> None:
    bundle = load_catalog(ROOT)

    def run() -> dict[str, Any]:
        with maintenance.begin() as connection:
            connection.execute(text("SET LOCAL ROLE app_owner"))
            return import_catalog(connection, bundle)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(result["created"] for result in results) == 1
    with maintenance.begin() as connection:
        connection.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            connection.execute(text("SELECT count(*) FROM endpoint_catalog")).scalar_one() == 1818
        )
        assert connection.execute(text("SELECT count(*) FROM catalog_imports")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM catalog_head")).scalar_one() == 1
        assert run()["created"] is False


def test_eight_connectors_remain_reference_only(
    maintenance: Engine,
    login_api: dict[str, Any],
) -> None:
    with maintenance.begin() as connection:
        connection.execute(text("SET LOCAL ROLE app_owner"))
        import_catalog(connection, load_catalog(ROOT))
    app = login_api["app"].state.identity_service.app
    with app.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM endpoint_catalog")).scalar_one() == 0
    identity = VerifiedIdentity(login_api["tenant"], login_api["member"], login_api["user"])
    with business_transaction(app, identity) as session:
        connection = session.connection()
        assert (
            connection.execute(text("SELECT count(*) FROM endpoint_catalog")).scalar_one() == 1818
        )
        ids = (
            connection.execute(text("SELECT connector_id FROM catalog_connectors")).scalars().all()
        )
        assert set(ids) == {
            "adq",
            "wxpromo",
            "beans",
            "wxshop",
            "qianchuan",
            "douyinshop",
            "juguang",
            "redshop",
        }
        for connector in ids:
            result = connector_catalog(connection, connector)
            assert result["operations"] == [] and result["availability"] == "NOT_INTEGRATED"
            assert result["referenceOnly"] and result["binding_status"] == "UNCONFIRMED"
        assert connector_catalog(connection, "beans")["reference_provider"] is None


def test_missing_methods_empty_paths_and_source_markers_never_enable_routes(
    maintenance: Engine,
) -> None:
    with maintenance.begin() as connection:
        connection.execute(text("SET LOCAL ROLE app_owner"))
        import_catalog(connection, load_catalog(ROOT))
        assert (
            connection.execute(
                text("SELECT count(*) FROM endpoint_catalog WHERE route_enabled")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM endpoint_catalog WHERE normalized_path IS NULL")
            ).scalar_one()
            == 50
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM endpoint_catalog WHERE http_method='UNKNOWN'")
            ).scalar_one()
            > 0
        )
    with pytest.raises(DBAPIError), maintenance.begin() as connection:
        connection.execute(text("SET LOCAL ROLE app_owner"))
        connection.execute(text("UPDATE endpoint_catalog SET route_enabled=true"))


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO catalog_head(singleton,catalog_sha256) "
        "SELECT true,sha256 FROM catalog_imports",
        "UPDATE endpoint_catalog SET http_method='POST'",
        "DELETE FROM endpoint_catalog",
        "SET LOCAL ROLE app_owner",
    ],
)
def test_runtime_cannot_change_catalog_or_grant_itself_execution(
    maintenance: Engine,
    login_api: dict[str, Any],
    statement: str,
) -> None:
    with maintenance.begin() as connection:
        connection.execute(text("SET LOCAL ROLE app_owner"))
        import_catalog(connection, load_catalog(ROOT))
    app = login_api["app"].state.identity_service.app
    identity = VerifiedIdentity(login_api["tenant"], login_api["member"], login_api["user"])
    with pytest.raises(DBAPIError), business_transaction(app, identity) as session:
        session.execute(text(statement))
