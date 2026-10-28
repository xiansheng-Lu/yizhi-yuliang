"""罗盘页与既有同步档案同事务，历史保留且不混合广告事实。"""

import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.commerce.compass_contracts import current_compass_contract
from yuliang.modules.commerce.compass_projection import compass_samples
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.sync_store import PagePermit, SyncPage
from yuliang.modules.reporting.models import SourceProof
from yuliang.modules.reporting.service import FactWriter


def save_compass(
    session: Session,
    ctx: GatewayContext,
    permit: PagePermit,
    day: date,
    page: SyncPage,
    writer: FactWriter,
) -> None:
    row = page.records[0].values
    contract = current_compass_contract(session, ctx)
    samples, gaps = compass_samples(ctx, day, row["values"], contract)
    proof = SourceProof(
        sync_run_id=permit.lease.run_id,
        page_no=permit.lease.page_no,
        outbound_id=permit.outbound_id,
        source_updated_at=None,
        fetched_at=page.fetched_at,
    )
    decisions = writer.apply(session, ctx, proof, samples) if samples else []
    row = row | dict(projection_gaps=gaps)
    session.execute(
        text("""INSERT INTO shop_compass_observations
      (tenant_id,account_id,shop_id,source_id,run_id,page_no,outbound_id,report_date,observation,fetched_at,contract_id,fact_decisions)
      VALUES(:tenant,:account,:shop,:source,:run,:page,:outbound,:day,
      CAST(:observation AS jsonb),:fetched,:contract,CAST(:decisions AS jsonb))"""),
        dict(
            tenant=ctx.identity.tenant_id,
            account=ctx.account_id,
            shop=ctx.shop_id,
            source=permit.lease.source_id,
            run=permit.lease.run_id,
            page=permit.lease.page_no,
            outbound=permit.outbound_id,
            day=day,
            observation=json.dumps(row),
            fetched=page.fetched_at,
            contract=contract.id if contract else None,
            decisions=json.dumps(decisions),
        ),
    )
