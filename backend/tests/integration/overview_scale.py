"""Multi-account normalized fixture through the actual AdqReportSync pipeline."""

import json
import secrets
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from pydantic import SecretBytes, SecretStr
from sqlalchemy import text

from yuliang.modules.catalog.service import link_authorization
from yuliang.modules.reporting.overview_models import OverviewRequest

from . import test_adq_reports as sdk
from .test_resource_queries import declare


async def build_scale(d: dict[str, Any], governance: Any, count: int) -> OverviewRequest:
    account_count, day_count = (2, 10) if count == 100 else (10, 100)
    first = date(2026, 1, 1)
    last = first + timedelta(days=day_count - 1)

    def upstream(req: httpx.Request) -> httpx.Response:
        day = json.loads(req.url.params["date_range"])["start_date"]
        native = int(req.url.params["account_id"])
        return httpx.Response(
            200,
            json=dict(
                code=0,
                data=dict(
                    list=[
                        dict(
                            account_id=native,
                            date=day,
                            view_count=20,
                            valid_click_count=5,
                            cost=100,
                            order_amount=300,
                            roi=3,
                        )
                    ],
                    page_info=dict(page=1, page_size=100, total_number=1, total_page=1),
                ),
            ),
        )

    key = SecretStr(secrets.token_urlsafe(48))
    client = sdk.GovernedPlatformClient(
        sdk.RecordedPlatformClient(
            sdk.PlatformHttpClient(sdk.native_signer, transport=httpx.MockTransport(upstream)),
            d["archive"],
            policy=sdk.REPORT_ARCHIVE_POLICY,
            decoder=sdk.decode_verdict,
        ),
        governance,
        d["app_id"],
    )

    async def token(_: Any) -> SecretStr:
        return SecretStr("synthetic-scale-token")

    reader = sdk.AdqReportSync(
        sdk.SyncStore(
            d["engine"],
            sdk.CursorCodec({1: SecretBytes(secrets.token_bytes(32))}, 1, key),
            policy=sdk.REPORT_MIRROR_POLICY,
        ),
        client,
        token,
        sdk.FactWriter(d["engine"], key),
        ("/v1.3/daily_reports/get",),
        ("read",),
    )
    scopes = []
    for i in range(account_count):
        account, binding_id = uuid4(), uuid4()
        ctx = replace(
            d["contexts"]["DAILY"],
            account_id=account,
            native_account_id=str(20000 + i),
            binding_id=binding_id,
        )
        profile = "ADQ_DAILY_REPORTS_V1_3_READ"
        route = sdk.ReadRouteSource(
            profile=profile,
            source_sha256=sdk.PROFILES[profile].source_sha256,
            checked_by="SYNTHETIC_SCALE",
            checked_at=datetime.now(UTC) - timedelta(minutes=1),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        binding = d["route_binding"].model_copy(
            update=dict(
                id=binding_id,
                account_id=account,
                catalog_id=ctx.catalog_id,
                catalog_sha256=ctx.catalog_sha256,
                normalized_path="/v1.3/daily_reports/get",
            )
        )
        with d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text(
                    "INSERT INTO ad_accounts "
                    "(id,tenant_id,entity_id,provider,product_line,native_id,name,status) "
                    "VALUES(:id,:tenant,:entity,'tencent','adq',:native,'Scale','AUTHORIZED')"
                ),
                dict(
                    id=account,
                    tenant=d["tenant"],
                    entity=d["context"].business_entity_id,
                    native=ctx.native_account_id,
                ),
            )
            owner.execute(
                text(
                    "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                    "VALUES(:tenant,:shop,:account)"
                ),
                dict(tenant=d["tenant"], shop=ctx.shop_id, account=account),
            )
            link_authorization(owner, ctx.authorization_id, account, "synthetic/overview/scale")
            sdk.register_binding(owner, binding, route_source=route)
            assertions = (
                owner.execute(
                    text("SELECT * FROM capability_assertions WHERE binding_id=:b"),
                    dict(b=d["contexts"]["DAILY"].binding_id),
                )
                .mappings()
                .all()
            )
            for row in assertions:
                proof = sdk.Evidence.model_validate({k: row[k] for k in sdk.Evidence.model_fields})
                sdk.record_evidence(
                    owner,
                    binding_id,
                    proof.model_copy(
                        update=dict(
                            id=uuid4(),
                            binding_id=binding_id,
                            sequence=0,
                            checked_at=max(proof.checked_at,route.checked_at),
                            expires_at=min(proof.expires_at,route.expires_at),
                            proof_ref=route.proof_ref
                            if proof.phase.value == "PATH_VERIFIED"
                            else proof.proof_ref,
                        )
                    ),
                )
        sdk.contract(d | dict(contexts=dict(DAILY=ctx)))
        declare(d, ctx, "reports")
        low = first
        while low <= last:
            high = min(last, low + timedelta(days=30))
            await reader.sync(
                ctx, uuid4(), sdk.query(start_date=low.isoformat(), end_date=high.isoformat())
            )
            low = high + timedelta(days=1)
        scopes.append(
            dict(
                tenantId=str(d["tenant"]),
                businessEntityId=str(d["context"].business_entity_id),
                shopId=str(ctx.shop_id),
                provider="tencent",
                productLine="adq",
                accountId=str(account),
                start=first.isoformat(),
                end=last.isoformat(),
            )
        )
    return OverviewRequest.model_validate(
        dict(
            accounts=scopes,
            filter=dict(
                scope=dict(
                    shop_id=str(d["shop"]),
                    live_session_id="all",
                    connector_id="adq",
                    date=last.isoformat(),
                    timezone="Asia/Shanghai",
                ),
                startDate=first.isoformat(),
                endDate=last.isoformat(),
            ),
        )
    )
