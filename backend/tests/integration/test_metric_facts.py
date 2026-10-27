"""真实PG事实/修订/并发/页原子性；所有供应商数据均为合成输入。"""

import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.service import register_binding
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncLease, SyncPage
from yuliang.modules.reporting.models import FactDimensions, MetricSample, SourceProof
from yuliang.modules.reporting.service import FACTS, FactConflict, FactWriter
from yuliang.platform.db import business_transaction

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api
from .test_platform_payloads import decoder, parameters, signer
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration
BASE = datetime(2026, 10, 26, tzinfo=UTC)


def sample(data: dict[str, Any], value: Any = 10, **changes: Any) -> MetricSample:
    ctx = data["ctx"]
    key = FactDimensions.model_validate(
        {
            "fact_date": date(2026, 10, 26),
            "granularity": "DAY",
            "provider": ctx.provider,
            "product_line": ctx.product_line,
            "entity_kind": "ACCOUNT",
            "native_entity_id": ctx.native_account_id,
            "shop_id": ctx.shop_id,
            "account_id": ctx.account_id,
            "metric_name": "spend_minor",
            "unit": "MINOR",
            "currency": "CNY",
            "attribution_window": "7d_click",
            "refund_policy": "GROSS",
        }
        | changes
    )
    return MetricSample(dimensions=key, value=value)


def writer(data: dict[str, Any]) -> FactWriter:
    if "writer" not in data:
        data["writer"] = FactWriter(data["engine"], SecretStr(secrets.token_urlsafe(48)))
    result: FactWriter = data["writer"]
    return result


async def deliver(
    data: dict[str, Any],
    rows: tuple[MetricSample, ...],
    *,
    version: int | None = 1,
    source_at: datetime | None = BASE,
    kind: str = "report.daily",
    apply: bool = True,
    mirrors: tuple[MirrorRecord, ...] = (),
) -> SourceProof:
    ctx, spec, store = data["ctx"], data["spec"], data["store"]
    lease = store.begin(ctx, spec, kind, uuid4(), parameters(ctx))
    assert isinstance(lease, SyncLease)
    permit = store.prepare(ctx, spec, lease)

    def platform(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "metrics": [row.model_dump(mode="json") for row in rows],
                "objects": [{"id": row.native_id} | row.values for row in mirrors],
            },
        )

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(platform)),
        data["archive"],
        decoder=decoder,
    )
    payload = parameters(ctx) | {"cursor": None}
    result = await client.send(
        ctx,
        spec,
        permit.outbound_id,
        payload,
        SecretStr("synthetic"),
        target=data["target"],
        before_persist=lambda session: store.dispatch_guard(session, ctx, spec, permit, payload),
    )
    assert result.exchange is not None
    proof = SourceProof(
        sync_run_id=lease.run_id,
        page_no=0,
        outbound_id=permit.outbound_id,
        source_updated_at=source_at,
        fetched_at=result.exchange.received_at,
        source_version=version,
        version_stream="synthetic-provider-version" if version is not None else None,
    )
    page = SyncPage(mirrors, None, True, proof.fetched_at, source_at, scanned_items=len(rows))
    decisions = []

    def project(session: Any) -> None:
        decisions.extend(writer(data).apply(session, ctx, proof, rows))

    data["last"] = (permit, page, proof, rows)
    store.commit_page(ctx, spec, permit, page, project_facts=project if apply else None)
    data["decisions"] = decisions
    return proof


def current(data: dict[str, Any]) -> list[Any]:
    with business_transaction(data["engine"], data["ctx"].identity) as session:
        return list(
            session.execute(text("SELECT * FROM metric_facts ORDER BY metric_name,id")).mappings()
        )


async def test_real_zero_missing_source_time_and_replay(sync_ready: dict[str, Any]) -> None:
    data = sync_ready
    rows = (sample(data, None, metric_name="paid_gmv_minor"), sample(data, 0))
    proof = await deliver(data, rows, version=0, source_at=None)
    assert data["decisions"] == ["MISSING", "APPLIED"]
    facts = current(data)
    assert len(facts) == 1 and facts[0]["value_num"] == 0 and facts[0]["source_updated_at"] is None
    assert facts[0]["fetched_at"] == proof.fetched_at and facts[0]["source_version"] == 0
    assert writer(data).ingest(data["ctx"], proof, rows) == ["MISSING", "APPLIED"]
    equivalent = (rows[0], MetricSample(dimensions=rows[1].dimensions, value=Decimal("0.000000")))
    assert writer(data).ingest(data["ctx"], proof, equivalent) == ["MISSING", "APPLIED"]
    with business_transaction(data["engine"], data["ctx"].identity) as session:
        assert session.execute(text("SELECT count(*) FROM metric_fact_revisions")).scalar_one() == 2
        assert (
            session.execute(
                text("SELECT items_count FROM sync_runs WHERE id=:id"), {"id": proof.sync_run_id}
            ).scalar_one()
            == 2
        )
    columns = [column.name for column in FACTS.c if column.name != "id"]
    with (
        pytest.raises(DBAPIError) as duplicate,
        business_transaction(data["engine"], data["ctx"].identity) as session,
    ):
        session.execute(
            insert(FACTS).from_select(columns, select(*(FACTS.c[name] for name in columns)))
        )
    assert getattr(duplicate.value.orig, "sqlstate", None) == "23505"


async def test_old_provider_version_cannot_overwrite_even_with_later_time(
    sync_ready: dict[str, Any],
) -> None:
    data = sync_ready
    await deliver(data, (sample(data, 11),), version=5, source_at=BASE)
    await deliver(data, (sample(data, 99),), version=4, source_at=BASE + timedelta(days=1))
    assert data["decisions"] == ["STALE"] and current(data)[0]["value_num"] == 11
    await deliver(data, (sample(data, 13),), version=6, source_at=None)
    fact = current(data)[0]
    assert (
        fact["value_num"] == 13
        and fact["revision_number"] == 2
        and fact["source_updated_at"] is None
    )
    with business_transaction(data["engine"], data["ctx"].identity) as session:
        assert session.execute(
            text("SELECT decision FROM metric_fact_revisions ORDER BY id")
        ).scalars().all() == ["APPLIED", "STALE", "APPLIED"]


async def test_unknown_time_uses_only_same_serial_source_watermark(
    sync_ready: dict[str, Any],
) -> None:
    data = sync_ready
    await deliver(data, (sample(data, 10),), version=None, source_at=None)
    await deliver(data, (sample(data, 20),), version=None, source_at=None)
    assert current(data)[0]["value_num"] == 20
    await deliver(data, (sample(data, 99),), version=None, source_at=None, kind="report.other")
    assert data["decisions"] == ["CONFLICT"] and current(data)[0]["value_num"] == 20


async def test_source_timestamp_order_and_missing_does_not_erase(
    sync_ready: dict[str, Any],
) -> None:
    data = sync_ready
    await deliver(data, (sample(data, 10),), version=None, source_at=BASE + timedelta(hours=2))
    await deliver(data, (sample(data, 1),), version=None, source_at=BASE + timedelta(hours=1))
    assert data["decisions"] == ["STALE"]
    await deliver(data, (sample(data, None),), version=None, source_at=None)
    assert data["decisions"] == ["MISSING"] and current(data)[0]["value_num"] == 10


async def test_live_sku_currency_attribution_refund_hour_keys_stay_separate(
    sync_ready: dict[str, Any],
) -> None:
    data = sync_ready
    ctx = data["ctx"]
    product, sku_a, sku_b, live_a, live_b = (uuid4() for _ in range(5))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO products(id,tenant_id,shop_id,native_id,name) "
                "VALUES(:id,:tenant,:shop,'product','Synthetic')"
            ),
            {"id": product, "tenant": ctx.identity.tenant_id, "shop": ctx.shop_id},
        )
        for sku, native in [(sku_a, "sku-a"), (sku_b, "sku-b")]:
            owner.execute(
                text(
                    "INSERT INTO skus(id,tenant_id,product_id,native_id) "
                    "VALUES(:id,:tenant,:product,:native)"
                ),
                {"id": sku, "tenant": ctx.identity.tenant_id, "product": product, "native": native},
            )
        for live in [live_a, live_b]:
            owner.execute(
                text(
                    "INSERT INTO live_sessions(id,tenant_id,shop_id,native_id) "
                    "VALUES(:id,:tenant,:shop,:native)"
                ),
                {
                    "id": live,
                    "tenant": ctx.identity.tenant_id,
                    "shop": ctx.shop_id,
                    "native": str(live),
                },
            )
    base = dict(
        entity_kind="SKU",
        native_entity_id="sku-a",
        product_id=product,
        sku_id=sku_a,
        live_id=live_a,
        metric_name="inventory_value",
    )
    changes: list[dict[str, Any]] = [
        {},
        {"sku_id": sku_b, "native_entity_id": "sku-b"},
        {"live_id": live_b},
        {"unit": "BEAN", "currency": "BEAN"},
        {"refund_policy": "NET_OF_REFUND"},
        {"attribution_window": "1d_click"},
        {"granularity": "HOUR", "fact_hour": 0},
        {"granularity": "HOUR", "fact_hour": 1},
    ]
    rows = tuple(sample(data, index + 1, **(base | change)) for index, change in enumerate(changes))
    await deliver(data, rows)
    assert len(current(data)) == 8 and data["decisions"] == ["APPLIED"] * 8
    with business_transaction(data["engine"], ctx.identity) as session:
        assert (
            session.execute(
                text("SELECT count(DISTINCT comparable_key) FROM metric_facts")
            ).scalar_one()
            == 4
        )


@pytest.mark.parametrize("mode", ["revision_privilege", "wrong_product", "wrong_scope"])
async def test_fact_failure_rolls_back_page_mirror_and_cursor(
    sync_ready: dict[str, Any], mode: str
) -> None:
    data = sync_ready
    ctx = data["ctx"]
    row = sample(data)
    if mode == "wrong_product":
        row = MetricSample(
            dimensions=FactDimensions.model_validate(
                row.dimensions.model_dump() | {"product_line": "shop"}
            ),
            value=10,
        )
    if mode == "wrong_scope":
        row = MetricSample(
            dimensions=FactDimensions.model_validate(
                row.dimensions.model_dump() | {"shop_id": uuid4()}
            ),
            value=10,
        )
    if mode == "revision_privilege":
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("REVOKE INSERT ON metric_fact_revisions FROM app_login"))
    try:
        with pytest.raises(DBAPIError if mode == "revision_privilege" else FactConflict):
            await deliver(
                data, (row,), mirrors=(MirrorRecord(native_id="456", values={"status": "ENABLED"}),)
            )
        with business_transaction(data["engine"], ctx.identity) as session:
            assert session.execute(text("SELECT count(*) FROM metric_facts")).scalar_one() == 0
            assert session.execute(text("SELECT count(*) FROM sync_pages")).scalar_one() == 0
            assert (
                session.execute(text("SELECT count(*) FROM sync_resource_mirrors")).scalar_one()
                == 0
            )
            assert session.execute(text("SELECT max(version) FROM sync_sources")).scalar_one() == 0
            assert session.execute(text("SELECT count(*) FROM platform_requests")).scalar_one() == 1
    finally:
        if mode == "revision_privilege":
            with data["maintenance"].begin() as owner:
                owner.execute(text("SET LOCAL ROLE app_owner"))
                owner.execute(text("GRANT INSERT ON metric_fact_revisions TO app_login"))


async def test_parallel_revisions_keep_newest_version_without_duplicate_fact(
    sync_ready: dict[str, Any],
) -> None:
    data = sync_ready
    pending = []
    for version in range(1, 9):
        rows = (sample(data, version),)
        proof = await deliver(data, rows, version=version, apply=False)
        pending.append((proof, rows))
    service = writer(data)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(lambda pair: service.ingest(data["ctx"], pair[0], pair[1]), reversed(pending))
        )
    facts = current(data)
    assert len(facts) == 1 and facts[0]["value_num"] == 8 and facts[0]["source_version"] == 8
    with business_transaction(data["engine"], data["ctx"].identity) as session:
        assert session.execute(text("SELECT count(*) FROM metric_fact_revisions")).scalar_one() == 8


async def test_ratio_zero_denominator_and_history_append_only(sync_ready: dict[str, Any]) -> None:
    data = sync_ready
    base = sample(data).dimensions
    key = FactDimensions.model_validate(
        base.model_dump()
        | {"unit": "RATIO", "currency": None, "basis_currency": "CNY", "metric_name": "roi"}
    )
    await deliver(data, (MetricSample(dimensions=key, numerator=100, denominator=10),))
    await deliver(data, (MetricSample(dimensions=key, numerator=0, denominator=1),), version=2)
    await deliver(data, (MetricSample(dimensions=key, numerator=0, denominator=0),), version=3)
    assert data["decisions"] == ["UNDEFINED_RATIO"]
    assert current(data)[0]["value_num"] == Decimal(0)
    for statement in [
        "UPDATE metric_fact_revisions SET decision='APPLIED'",
        "DELETE FROM metric_fact_revisions",
    ]:
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["ctx"].identity) as session,
        ):
            session.execute(text(statement))


async def test_account_and_tenant_isolation_and_forged_source_refused(
    sync_ready: dict[str, Any],
) -> None:
    data = sync_ready
    first = sample(data)
    proof = await deliver(data, (first,))
    with pytest.raises(FactConflict, match="事实来源"):
        writer(data).ingest(
            data["ctx"], proof.model_copy(update={"outbound_id": uuid4()}), (first,)
        )
    new_account, new_binding = uuid4(), uuid4()
    ctx = data["ctx"]
    binding = data["binding"].model_copy(
        update={
            "id": new_binding,
            "account_id": new_account,
            "operation_kind": "READ",
            "http_method": "GET",
            "normalized_path": data["spec"].path,
            "catalog_id": ctx.catalog_id,
            "permission_code": "overview.read",
            "action": "analyze",
        }
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO ad_accounts(id,tenant_id,provider,product_line,native_id,name)
            VALUES(:id,:tenant,'tencent','adq',:native,'Synthetic other account')"""),
            {"id": new_account, "tenant": ctx.identity.tenant_id, "native": str(new_account)},
        )
        owner.execute(
            text("""INSERT INTO entity_links(tenant_id,shop_id,account_id)
            VALUES(:tenant,:shop,:account)"""),
            {"tenant": ctx.identity.tenant_id, "shop": ctx.shop_id, "account": new_account},
        )
        register_binding(owner, binding)
    second = data | {
        "ctx": replace(
            ctx, account_id=new_account, native_account_id=str(new_account), binding_id=new_binding
        ),
        "target": data["target"].model_copy(update={"account_id": new_account}),
    }
    second_row = sample(second, 20)
    await deliver(second, (second_row,))
    assert len(current(data)) == 2
    with pytest.raises(FactConflict):
        writer(data).ingest(ctx, proof, (second_row,))
    with business_transaction(data["engine"], replace(ctx.identity, tenant_id=999999)) as session:
        assert session.execute(text("SELECT count(*) FROM metric_facts")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM metric_fact_revisions")).scalar_one() == 0
