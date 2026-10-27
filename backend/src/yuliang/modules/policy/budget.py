"""完整来源合同与预算B刷新；数值只在PG从已确认全量页聚合，不接受调用方预算。"""

import re
from typing import Literal
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.audit.service import record
from yuliang.modules.capability.service import maintenance
from yuliang.modules.connectors.freshness import ResourcePolicy
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


def register_budget_account(
    connection: Connection,
    account_id: UUID,
    currency: Literal["CNY", "BEAN"],
    proof_ref: str,
    *,
    source_id: UUID | None = None,
    authorization_id: UUID | None = None,
    empty_cursor_digest: str | None = None,
    budget_field: str = "budget_minor",
    state_field: str = "enabled",
    currency_field: str = "currency",
) -> None:
    """维护人核对真实接口的全配置/含暂停过滤语义；不能由运行用户自称覆盖完整。"""
    maintenance(connection)
    if currency not in {"CNY", "BEAN"} or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,180}", proof_ref):
        raise ValueError("账户币种与完整配置来源需明确证明")
    account = (
        connection.execute(text("SELECT * FROM ad_accounts WHERE id=:id"), {"id": account_id})
        .mappings()
        .one()
    )
    tenant = account["tenant_id"]
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760210))"), {"key": str(tenant)}
    )
    values = dict(
        tenant=tenant,
        account=account_id,
        currency=currency,
        source=source_id,
        auth=authorization_id,
        empty=empty_cursor_digest,
        budget=budget_field,
        state=state_field,
        unit=currency_field,
        filter=None,
        revision=None,
        proof=proof_ref,
    )
    if currency == "CNY":
        if (
            source_id is None
            or authorization_id is None
            or empty_cursor_digest is None
            or not re.fullmatch(r"[0-9a-f]{64}", empty_cursor_digest)
        ):
            raise ValueError("CNY预算需完整源、明确授权和从空游标开始的证明")
        for field in (budget_field, state_field, currency_field):
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", field):
                raise ValueError("预算字段合同不合法")
        source = (
            connection.execute(
                text("""SELECT s.*,p.revision AS policy_revision,p.target_mode,
            p.resource_key,p.max_age_seconds,p.refresh_seconds,p.clock_kind,p.required_fields,p.required_metrics
            FROM sync_sources s JOIN resource_freshness_policies p
            ON (p.tenant_id,p.source_id)=(s.tenant_id,s.id)
            WHERE s.tenant_id=:tenant AND s.id=:id AND s.account_id=:account"""),
                {"tenant": tenant, "id": source_id, "account": account_id},
            )
            .mappings()
            .one()
        )
        policy = ResourcePolicy.model_validate(
            {key: source[key] for key in ResourcePolicy.model_fields}
        )
        if policy.target_mode != "ACCOUNT" or not {
            (budget_field, "INTEGER"),
            (state_field, "BOOLEAN"),
            (currency_field, "CODE"),
        } <= set(policy.required_fields):
            raise ValueError("预算来源必须是完整账户列表并有明确金额/状态/币种字段")
        auth = connection.execute(
            text("""SELECT id FROM provider_authorizations WHERE tenant_id=:tenant
            AND id=:id AND provider=:provider AND product_line=:product
            AND (account_id IS NULL OR account_id=:account)"""),
            {
                "tenant": tenant,
                "id": authorization_id,
                "provider": account["provider"],
                "product": account["product_line"],
                "account": account_id,
            },
        ).scalar_one_or_none()
        if auth is None:
            raise ValueError("预算来源授权上下文不一致")
        values.update(filter=source["filter_digest"], revision=source["policy_revision"])
    elif any(value is not None for value in (source_id, authorization_id, empty_cursor_digest)):
        raise ValueError("BEAN分类独立于CNY来源，不借用CNY初始化")
    connection.execute(
        text("""INSERT INTO budget_account_contracts(tenant_id,account_id,currency,
        source_id,authorization_id,source_policy_revision,filter_digest,empty_cursor_digest,
        budget_field,state_field,currency_field,coverage_kind,proof_ref)
        VALUES(:tenant,:account,:currency,:source,:auth,:revision,:filter,:empty,:budget,:state,:unit,
        'ALL_CONFIGURED_INCLUDING_PAUSED',:proof) ON CONFLICT(tenant_id,account_id) DO UPDATE SET
        currency=EXCLUDED.currency,source_id=EXCLUDED.source_id,authorization_id=EXCLUDED.authorization_id,
        source_policy_revision=EXCLUDED.source_policy_revision,filter_digest=EXCLUDED.filter_digest,
        empty_cursor_digest=EXCLUDED.empty_cursor_digest,budget_field=EXCLUDED.budget_field,
        state_field=EXCLUDED.state_field,currency_field=EXCLUDED.currency_field,proof_ref=EXCLUDED.proof_ref,
        revision=budget_account_contracts.revision+1
        WHERE (budget_account_contracts.currency,budget_account_contracts.source_id,
         budget_account_contracts.authorization_id,budget_account_contracts.source_policy_revision,
         budget_account_contracts.filter_digest,budget_account_contracts.empty_cursor_digest,
         budget_account_contracts.budget_field,budget_account_contracts.state_field,
         budget_account_contracts.currency_field,budget_account_contracts.proof_ref)
        IS DISTINCT FROM (EXCLUDED.currency,EXCLUDED.source_id,EXCLUDED.authorization_id,
         EXCLUDED.source_policy_revision,EXCLUDED.filter_digest,EXCLUDED.empty_cursor_digest,
         EXCLUDED.budget_field,EXCLUDED.state_field,EXCLUDED.currency_field,EXCLUDED.proof_ref)"""),
        values,
    )


class BudgetInitialization:
    def __init__(self, engine: Engine, identity: VerifiedIdentity, request_id: UUID) -> None:
        self.engine, self.identity, self.request_id = engine, identity, request_id

    def refresh(self, expected_policy: int) -> dict[str, str | bool]:
        if type(expected_policy) is not int or expected_policy < 1:
            raise ApiError(ErrorCode.INVALID_INPUT, "需要明确当前策略版本")
        try:
            with business_transaction(self.engine, self.identity) as session:
                value: dict[str, str | bool] = session.execute(
                    text("SELECT access.budget_initialize(:member,:user,:request,:policy)"),
                    {
                        "member": self.identity.membership_id,
                        "user": self.identity.user_id,
                        "request": self.request_id,
                        "policy": expected_policy,
                    },
                ).scalar_one()
                record(
                    session,
                    self.identity,
                    {
                        "action": "budget.initialize",
                        "object_type": "budget_ledger",
                        "object_id": UUID(str(value["ledgerId"])),
                        "request_id": self.request_id,
                    },
                )
                digest = value.get("governanceDigest")
                if isinstance(digest, str):
                    record(
                        session,
                        self.identity,
                        {
                            "action": "budget.observe",
                            "object_type": "budget_ledger",
                            "object_id": UUID(str(value["ledgerId"])),
                            "request_id": self.request_id,
                            "after_hash": digest,
                        },
                    )
                return value
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None)
            mapping = {
                "42501": ErrorCode.FORBIDDEN,
                "40001": ErrorCode.POLICY_VERSION_CHANGED,
                "22023": ErrorCode.DATA_COVERAGE_INSUFFICIENT,
            }
            if code in mapping:
                raise ApiError(mapping[code], "预算来源、覆盖范围或策略需要重新核验") from None
            raise
