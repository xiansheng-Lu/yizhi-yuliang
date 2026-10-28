"""按页原子投影、不可变配置修订与独立计划店铺关系。"""

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.capability.service import maintenance
from yuliang.modules.connectors.adq_assets import AssetKind, AssetPage
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.redaction import canonical
from yuliang.modules.connectors.sync_store import PagePermit, SyncConflict
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


def project_assets(
    session: Session, ctx: GatewayContext, kind: AssetKind, permit: PagePermit, page: AssetPage
) -> None:
    lease = permit.lease
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760122))"),
        dict(key=f"{ctx.identity.tenant_id}|{ctx.account_id}|{kind}"),
    )
    previous = session.execute(
        text("""SELECT page_size,total_number,total_page FROM adq_asset_pages
      WHERE run_id=:run ORDER BY page_no LIMIT 1"""),
        dict(run=lease.run_id),
    ).first()
    if previous is not None and tuple(previous) != (
        page.page_size,
        page.total_number,
        page.total_page,
    ):
        raise SyncConflict("PAGINATION_CHANGED")
    proof = dict(
        tenant=ctx.identity.tenant_id,
        source=lease.source_id,
        run=lease.run_id,
        page=lease.page_no,
        outbound=permit.outbound_id,
        account=ctx.account_id,
        shop=ctx.shop_id,
        kind=kind,
        fetched=page.sync.fetched_at,
    )
    session.execute(
        text("""INSERT INTO adq_asset_pages(tenant_id,source_id,run_id,page_no,
      outbound_id,kind,wire_page,page_size,total_number,total_page)
      VALUES(:tenant,:source,:run,:page,:outbound,:kind,:wire,:size,:total,:pages)"""),
        proof
        | dict(
            wire=page.wire_page, size=page.page_size, total=page.total_number, pages=page.total_page
        ),
    )
    for item in page.sync.records:
        config = item.values
        digest = hashlib.sha256(canonical(config)).hexdigest()
        old = (
            session.execute(
                text("""SELECT * FROM adq_asset_mirrors
          WHERE account_id=:account AND kind=:kind AND native_id=:native FOR UPDATE"""),
                proof | dict(native=item.native_id),
            )
            .mappings()
            .first()
        )
        asset_id = old["id"] if old else uuid4()
        already = session.execute(
            text("""SELECT 1 FROM adq_asset_observations
          WHERE run_id=:run AND asset_id=:asset"""),
            proof | dict(asset=asset_id),
        ).first()
        if already:
            raise SyncConflict("DUPLICATE_ASSET_ACROSS_PAGES")
        if old and old["protocol_version"] != config["protocol_version"]:
            raise SyncConflict("ASSET_PROTOCOL_CHANGED")
        applied = old is None or page.sync.fetched_at >= old["fetched_at"]
        if old and page.sync.fetched_at == old["fetched_at"] and old["config_digest"] != digest:
            raise SyncConflict("CONFLICTING_ASSET_OBSERVATION")
        changed = applied and (old is None or old["config_digest"] != digest)
        version = 1 if old is None else old["version"] + int(changed)
        values = proof | dict(
            asset=asset_id,
            native=item.native_id,
            protocol=config["protocol_version"],
            config=json.dumps(config, ensure_ascii=True),
            digest=digest,
            version=version,
        )
        if old is None:
            session.execute(
                text("""INSERT INTO adq_asset_mirrors(id,tenant_id,account_id,kind,
              native_id,protocol_version,config,config_digest,version,source_shop_id,source_id,
              run_id,page_no,outbound_id,fetched_at)
              VALUES(:asset,:tenant,:account,:kind,:native,:protocol,CAST(:config AS jsonb),
               :digest,:version,:shop,:source,:run,:page,:outbound,:fetched)"""),
                values,
            )
        elif applied:
            session.execute(
                text("""UPDATE adq_asset_mirrors SET config=CAST(:config AS jsonb),
              config_digest=:digest,version=:version,source_shop_id=:shop,source_id=:source,
              run_id=:run,page_no=:page,outbound_id=:outbound,fetched_at=:fetched,
              source_updated_at=NULL WHERE id=:asset"""),
                values,
            )
        session.execute(
            text("""INSERT INTO adq_asset_observations(tenant_id,asset_id,run_id,
          page_no,outbound_id,applied,fetched_at)
          VALUES(:tenant,:asset,:run,:page,:outbound,:applied,:fetched)"""),
            values | dict(applied=applied),
        )
        if changed:
            session.execute(
                text("""INSERT INTO adq_asset_versions(tenant_id,asset_id,version,
              before_json,after_json,change_source,run_id,page_no,outbound_id,fetched_at)
              VALUES(:tenant,:asset,:version,CAST(:before AS jsonb),CAST(:config AS jsonb),
               'PLATFORM_READ',:run,:page,:outbound,:fetched)"""),
                values | dict(before=json.dumps(old["config"]) if old else None),
            )


def register_campaign_shop(
    connection: Connection,
    tenant_id: int,
    asset_id: UUID,
    shop_id: UUID,
    proof_ref: str,
    checked_at: datetime,
    expires_at: datetime,
) -> UUID:
    maintenance(connection)
    if any(value.tzinfo is None or value.utcoffset() is None for value in (checked_at, expires_at)):
        raise ValueError("计划店铺关系核验时间必须包含时区")
    now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    if (
        not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,180}", proof_ref)
        or not checked_at <= now < expires_at
    ):
        raise ValueError("计划店铺关联需要独立、当前有效的真实关系依据")
    asset = (
        connection.execute(
            text("SELECT * FROM adq_asset_mirrors WHERE tenant_id=:tenant AND id=:id FOR UPDATE"),
            dict(tenant=tenant_id, id=asset_id),
        )
        .mappings()
        .one()
    )
    if asset["kind"] != "CAMPAIGN" or asset["protocol_version"] != "1.3":
        raise ValueError("只接纳已核对原生层级的计划，不把营销单元替代为计划")
    prior = (
        connection.execute(
            text("SELECT * FROM adq_campaign_shop_links WHERE tenant_id=:tenant AND asset_id=:id"),
            dict(tenant=tenant_id, id=asset_id),
        )
        .mappings()
        .first()
    )
    if prior:
        if prior["shop_id"] != shop_id:
            raise ValueError("已有计划店铺关系不能覆盖或借用到另一店铺")
        return UUID(str(prior["campaign_id"]))
    campaign_id = uuid4()
    connection.execute(
        text("""INSERT INTO campaigns(id,tenant_id,shop_id,account_id,
      provider,product_line,native_id,kind,protocol_version)
      VALUES(:id,:tenant,:shop,:account,'tencent','adq',:native,'CAMPAIGN','1.3')"""),
        dict(
            id=campaign_id,
            tenant=tenant_id,
            shop=shop_id,
            account=asset["account_id"],
            native=asset["native_id"],
        ),
    )
    connection.execute(
        text("""INSERT INTO adq_campaign_shop_links(tenant_id,asset_id,account_id,
      shop_id,campaign_id,proof_ref,checked_at,expires_at)
      VALUES(:tenant,:asset,:account,:shop,:campaign,:proof,:checked,:expires)"""),
        dict(
            tenant=tenant_id,
            asset=asset_id,
            account=asset["account_id"],
            shop=shop_id,
            campaign=campaign_id,
            proof=proof_ref,
            checked=checked_at,
            expires=expires_at,
        ),
    )
    return campaign_id


def resolve_campaign(session: Session, ctx: GatewayContext, asset_id: UUID) -> UUID:
    require_shop_account(session, ctx.identity, ctx.shop_id, ctx.account_id, "overview.read")
    row = session.execute(
        text("""SELECT l.campaign_id FROM adq_campaign_shop_links l
      JOIN adq_asset_mirrors m ON (m.tenant_id,m.id)=(l.tenant_id,l.asset_id)
      JOIN campaigns c ON (c.tenant_id,c.id)=(l.tenant_id,l.campaign_id)
      WHERE l.asset_id=:asset AND l.account_id=:account AND l.shop_id=:shop
       AND l.checked_at<=clock_timestamp() AND l.expires_at>clock_timestamp()
       AND c.kind='CAMPAIGN' AND c.protocol_version='1.3' AND m.native_id=c.native_id
       AND c.account_id=l.account_id AND c.shop_id=l.shop_id"""),
        dict(asset=asset_id, account=ctx.account_id, shop=ctx.shop_id),
    ).first()
    if row is None:
        raise ApiError(ErrorCode.NO_DATA, "计划缺少当前店铺关系依据，不能拼装为目标")
    # 这仅是只读内部ID映射；旧版CAMPAIGN不能用于v3.0 ADGROUP写操作。
    return UUID(str(row.campaign_id))


def list_assets(engine: Engine, ctx: GatewayContext, kind: AssetKind) -> list[dict[str, Any]]:
    with business_transaction(engine, ctx.identity) as session:
        require_shop_account(session, ctx.identity, ctx.shop_id, ctx.account_id, "overview.read")
        authorized = session.execute(
            text("""SELECT EXISTS(SELECT 1 FROM provider_authorizations p
          JOIN authorization_account_links l ON l.authorization_id=p.id AND l.tenant_id=p.tenant_id
          JOIN ad_accounts a ON (a.tenant_id,a.id)=(l.tenant_id,l.account_id)
          WHERE p.id=:grant AND a.id=:account AND l.native_id_snapshot=a.native_id
           AND a.native_id=:native AND a.status='AUTHORIZED' AND a.deleted_at IS NULL
           AND p.status='ACTIVE' AND p.expires_at>clock_timestamp()
           AND cardinality(p.scopes)>0)"""),
            dict(grant=ctx.authorization_id, account=ctx.account_id, native=ctx.native_account_id),
        ).scalar_one()
        rows = (
            session.execute(
                text("""SELECT id,native_id,config,version,fetched_at,source_updated_at,
          outbound_id FROM adq_asset_mirrors WHERE account_id=:account AND kind=:kind
          ORDER BY native_id LIMIT 1001"""),
                dict(account=ctx.account_id, kind=kind),
            )
            .mappings()
            .all()
        )
        if len(rows) > 1000:
            raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "资产目录需要继续分页读取")
        return [
            dict(row)
            | dict(
                bound=True,
                authorization="AUTHORIZED" if authorized else "UNAUTHORIZED",
                executable=False,
            )
            for row in rows
        ]
