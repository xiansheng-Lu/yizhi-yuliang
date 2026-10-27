"""仅接收已确认只读页，规范事实与追加历史可加入分页提交事务。"""

import json
from collections.abc import Sequence
from datetime import UTC
from decimal import Decimal

from pydantic import SecretStr
from sqlalchemy import Engine, column, insert, select, table, text, update
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.redaction import canonical, fingerprint
from yuliang.modules.reporting.models import FactDimensions, MetricSample, SourceProof
from yuliang.modules.reporting.revisions import SourceStamp, compare
from yuliang.platform.db import business_transaction

DIMENSION_COLUMNS = tuple(FactDimensions.model_fields)
MUTABLE_COLUMNS = (
    "value_num",
    "numerator",
    "denominator",
    "source_updated_at",
    "fetched_at",
    "source_version",
    "version_stream",
    "source_id",
    "sync_run_id",
    "page_no",
    "outbound_id",
    "confirmed_sequence",
)
FACTS = table(
    "metric_facts",
    *(
        column(name)
        for name in ("id", "tenant_id", "revision_number", *DIMENSION_COLUMNS, *MUTABLE_COLUMNS)
    ),
)


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    raw = format(value, "f")
    return raw.rstrip("0").rstrip(".") if "." in raw else raw


class FactConflict(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__("事实来源或口径核对不通过")
        self.reason = reason


class FactWriter:
    def __init__(self, engine: Engine, audit_key: SecretStr) -> None:
        fingerprint(audit_key, "metric-key-check", b"")
        self.engine, self._key = engine, audit_key

    @staticmethod
    def _scope(session: Session, ctx: GatewayContext, key: FactDimensions) -> None:
        if (
            (key.provider, key.product_line, key.shop_id)
            != (ctx.provider, ctx.product_line, ctx.shop_id)
            or key.account_id is not None
            and key.account_id != ctx.account_id
        ):
            raise FactConflict("SCOPE_MISMATCH")
        require_shop_account(session, ctx.identity, ctx.shop_id, ctx.account_id, "overview.read")
        params = {
            "tenant": ctx.identity.tenant_id,
            "shop": key.shop_id,
            "account": key.account_id,
            "campaign": key.campaign_id,
            "live": key.live_id,
            "product": key.product_id,
            "sku": key.sku_id,
        }
        statements = {
            "SHOP": "SELECT native_id FROM shops WHERE id=:shop AND deleted_at IS NULL",
            "ACCOUNT": "SELECT native_id FROM ad_accounts WHERE id=:account AND deleted_at IS NULL",
            "CAMPAIGN": "SELECT native_id FROM campaigns WHERE id=:campaign "
            "AND account_id=:account AND shop_id=:shop",
            "LIVE": "SELECT native_id FROM live_sessions WHERE id=:live AND shop_id=:shop",
            "PRODUCT": "SELECT native_id FROM products WHERE id=:product "
            "AND shop_id=:shop AND deleted_at IS NULL",
            "SKU": """SELECT s.native_id FROM skus s JOIN products p
                ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id) WHERE s.id=:sku
                AND s.product_id=:product AND p.shop_id=:shop AND p.deleted_at IS NULL""",
        }
        if key.entity_kind == "VIDEO":
            if key.account_id != ctx.account_id:
                raise FactConflict("SCOPE_MISMATCH")
            return
        native = session.execute(text(statements[key.entity_kind]), params).scalar_one_or_none()
        if native is None or native != key.native_entity_id:
            raise FactConflict("NATIVE_ENTITY_MISMATCH")

    @staticmethod
    def _proof(session: Session, ctx: GatewayContext, proof: SourceProof) -> SourceStamp:
        row = (
            session.execute(
                text("""SELECT r.source_id,r.source_version-r.page_no+p.page_no+1 AS sequence
          FROM sync_pages p JOIN sync_runs r ON (r.tenant_id,r.id)=(p.tenant_id,p.run_id)
          JOIN sync_sources s ON (s.tenant_id,s.id)=(r.tenant_id,r.source_id)
          JOIN platform_requests q ON (q.tenant_id,q.id)=(p.tenant_id,p.outbound_id)
          WHERE p.run_id=:run AND p.page_no=:page AND p.outbound_id=:outbound
           AND r.user_id=:user AND s.account_id=:account AND s.binding_id=:binding
           AND q.account_id=:account AND q.binding_id=:binding AND q.effect='READ'
           AND q.provider=:provider AND q.product_line=:product
           AND EXISTS(SELECT 1 FROM platform_raw_payloads raw WHERE raw.outbound_id=q.id
             AND raw.tenant_id=q.tenant_id AND raw.external_outcome='READ_SUCCEEDED')"""),
                {
                    "run": proof.sync_run_id,
                    "page": proof.page_no,
                    "outbound": proof.outbound_id,
                    "user": ctx.identity.user_id,
                    "account": ctx.account_id,
                    "binding": ctx.binding_id,
                    "provider": ctx.provider,
                    "product": ctx.product_line,
                },
            )
            .mappings()
            .first()
        )
        if row is None or row["sequence"] < 1:
            raise FactConflict("NO_CONFIRMED_PAGE")
        return SourceStamp(
            row["source_id"],
            row["sequence"],
            proof.source_updated_at,
            proof.source_version,
            proof.version_stream,
        )

    def ingest(
        self, ctx: GatewayContext, proof: SourceProof, samples: Sequence[MetricSample]
    ) -> list[str]:
        with business_transaction(self.engine, ctx.identity) as session:
            return self.apply(session, ctx, proof, samples)

    def apply(
        self,
        session: Session,
        ctx: GatewayContext,
        proof: SourceProof,
        samples: Sequence[MetricSample],
    ) -> list[str]:
        if len(samples) > 1000:
            raise ValueError("每页事实不能超过1000条")
        incoming = self._proof(session, ctx, proof)
        keyed = [
            (
                fingerprint(
                    self._key,
                    "metric-business-key-v1",
                    canonical(
                        {
                            "tenant_id": ctx.identity.tenant_id,
                            "key": sample.dimensions.business_key(),
                        }
                    ),
                ),
                sample,
            )
            for sample in samples
        ]
        if len({key for key, _ in keyed}) != len(keyed):
            raise FactConflict("DUPLICATE_DIMENSIONS_IN_PAGE")
        decisions: dict[str, str] = {}
        for key_digest, sample in sorted(keyed, key=lambda item: item[0]):
            self._scope(session, ctx, sample.dimensions)
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760115))"),
                {"key": key_digest},
            )
            decisions[key_digest] = self._one(session, ctx, proof, incoming, key_digest, sample)
        return [decisions[key] for key, _ in keyed]

    def _one(
        self,
        session: Session,
        ctx: GatewayContext,
        proof: SourceProof,
        incoming: SourceStamp,
        key_digest: str,
        sample: MetricSample,
    ) -> str:
        key = sample.dimensions
        source_payload = proof.model_dump(mode="json") | {
            "source_updated_at": proof.source_updated_at.astimezone(UTC).isoformat()
            if proof.source_updated_at
            else None,
            "fetched_at": proof.fetched_at.astimezone(UTC).isoformat(),
        }
        payload_digest = fingerprint(
            self._key,
            "metric-revision-v1",
            canonical(
                {
                    "sample": {
                        "dimensions": key.business_key(),
                        "value": _decimal(sample.value),
                        "numerator": _decimal(sample.numerator),
                        "denominator": _decimal(sample.denominator),
                    },
                    "source": source_payload,
                }
            ),
        )
        previous = (
            session.execute(
                text("""SELECT payload_digest,decision FROM metric_fact_revisions
            WHERE key_digest=:key AND outbound_id=:outbound"""),
                {"key": key_digest, "outbound": proof.outbound_id},
            )
            .mappings()
            .first()
        )
        if previous:
            if previous["payload_digest"] != payload_digest:
                raise FactConflict("IDEMPOTENCY_MISMATCH")
            return str(previous["decision"])
        dimensions = {name: getattr(key, name) for name in DIMENSION_COLUMNS}
        current = (
            session.execute(
                select(FACTS)
                .where(
                    FACTS.c.tenant_id == ctx.identity.tenant_id,
                    *(
                        FACTS.c[name].is_not_distinct_from(value)
                        for name, value in dimensions.items()
                    ),
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        value = sample.normalized_value
        if key.refund_policy == "UNKNOWN" or key.attribution_window.upper() in {
            "UNKNOWN",
            "UNCONFIRMED",
        }:
            decision = "CONFLICT"
        elif value is None:
            decision = "UNDEFINED_RATIO" if sample.numerator is not None else "MISSING"
        elif current is None:
            decision = "APPLIED"
        else:
            old = SourceStamp(
                current["source_id"],
                current["confirmed_sequence"],
                current["source_updated_at"],
                current["source_version"],
                current["version_stream"],
            )
            same = (current["value_num"], current["numerator"], current["denominator"]) == (
                value,
                sample.numerator,
                sample.denominator,
            )
            decision = compare(old, incoming, same_value=same)
        mutable = {
            "value_num": value,
            "numerator": sample.numerator,
            "denominator": sample.denominator,
            "source_updated_at": proof.source_updated_at,
            "fetched_at": proof.fetched_at,
            "source_version": proof.source_version,
            "version_stream": proof.version_stream,
            "source_id": incoming.source_id,
            "sync_run_id": proof.sync_run_id,
            "page_no": proof.page_no,
            "outbound_id": proof.outbound_id,
            "confirmed_sequence": incoming.sequence,
        }
        fact_id = current["id"] if current else None
        revision = current["revision_number"] if current else None
        if decision == "APPLIED":
            if current is None:
                values = dimensions | mutable | {"tenant_id": ctx.identity.tenant_id}
                fact_id = session.execute(
                    insert(FACTS).values(**values).returning(FACTS.c.id)
                ).scalar_one()
                revision = 1
            else:
                revision = current["revision_number"] + 1
                session.execute(
                    update(FACTS)
                    .where(
                        FACTS.c.tenant_id == ctx.identity.tenant_id,
                        FACTS.c.id == fact_id,
                        FACTS.c.fact_date == key.fact_date,
                    )
                    .values(**mutable, revision_number=revision)
                )
        session.execute(
            text("""INSERT INTO metric_fact_revisions(tenant_id,fact_id,fact_date,key_digest,
            key_json,payload_digest,decision,value_num,numerator,denominator,source_updated_at,fetched_at,
            source_version,version_stream,source_id,sync_run_id,page_no,outbound_id,confirmed_sequence,
            applied_revision) VALUES(:tenant,:fact,:date,:key,CAST(:json AS jsonb),
            :digest,:decision,
            :value_num,:numerator,:denominator,:source_updated_at,:fetched_at,:source_version,:version_stream,
            :source_id,:sync_run_id,:page_no,:outbound_id,:confirmed_sequence,:revision)"""),
            mutable
            | {
                "tenant": ctx.identity.tenant_id,
                "fact": fact_id,
                "date": key.fact_date,
                "key": key_digest,
                "json": json.dumps(key.business_key()),
                "digest": payload_digest,
                "decision": decision,
                "revision": revision if decision == "APPLIED" else None,
            },
        )
        return decision
