"""确定性规则消费PG真值；初始化或核验依据缺失时拒绝，不临时补0。"""

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.command.jobs import MoneyValue
from yuliang.modules.policy.models import CheckEvidence, RiskContext, RiskStage
from yuliang.modules.policy.service import check
from yuliang.modules.policy.thresholds import amount_limit, change_bound, frequency_bound
from yuliang.platform.errors import ApiError, ErrorCode

if TYPE_CHECKING:
    from yuliang.modules.policy.intent import IntentBindingRule

STAGES = frozenset(RiskStage)


class AmountLimitRule:
    name = "AMOUNT_LIMIT"
    stages = STAGES

    def evaluate(self, session: Session, context: RiskContext) -> CheckEvidence:
        config = context.config
        if config is None:
            return check(
                self.name, False, "需要有效的预算策略", {"reason": "POLICY_NOT_CONFIGURED"}
            )
        account = context.target["account_id"]
        rows = (
            session.execute(
                text("""SELECT * FROM budget_ledgers WHERE policy_id=:policy
        AND (account_id IS NULL OR account_id=:account) ORDER BY id"""),
                {"policy": context.policy_id, "account": account},
            )
            .mappings()
            .all()
        )
        roots = [
            row for row in rows if row["account_id"] is None and row["bucket_key"] == "TENANT_DAILY"
        ]
        if len(roots) != 1:
            return check(
                self.name, False, "预算核验基数尚未就绪", {"reason": "BUDGET_NOT_INITIALIZED"}
            )
        day = context.checked_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        credited = []
        for row in rows:
            ready = (
                row["policy_version"] == context.policy_version
                and row["currency"] == "CNY"
                and row["period_start"] == day
                and row["data_state"] == "CONFIRMED"
                and row["coverage_kind"] == "ALL_CONFIGURED_INCLUDING_PAUSED"
                and account in row["covered_account_ids"]
                and row["source_ref"] is not None
                and row["fetched_at"] is not None
                and row["valid_until"] is not None
                and row["fetched_at"] <= context.checked_at <= row["valid_until"]
                and row["freeze_reason"] in (None, "OVER_LIMIT")
                and (
                    row["account_id"] is not None or row["limit_minor"] == config.daily_budget_minor
                )
                and row["limit_minor"] <= config.daily_budget_minor
            )
            if not ready:
                return check(
                    self.name,
                    False,
                    "预算来源、周期或币种需要重新核验",
                    {"reason": "BUDGET_BASIS_NOT_CURRENT", "ledgerId": str(row["id"])},
                )
            if (
                row["source_ref"].startswith("budget-observation/")
                and not session.execute(
                    text("SELECT access.budget_basis_current(:id)"), {"id": row["id"]}
                ).scalar_one()
            ):
                return check(
                    self.name,
                    False,
                    "完整预算来源已变化，需重新核验",
                    {"reason": "BUDGET_COVERAGE_CHANGED", "ledgerId": str(row["id"])},
                )
            own = session.execute(
                text("SELECT access.budget_own_hold(:member,:user,:job,:ledger,:stage)"),
                {
                    "member": context.identity.membership_id,
                    "user": context.identity.user_id,
                    "job": context.job_id,
                    "ledger": row["id"],
                    "stage": context.stage.value,
                },
            ).scalar_one()
            expected_hold = (
                max(context.request.new_value.amount_minor - context.current_value.amount_minor, 0)
                if context.request.action_type == "CHANGE_DAILY_BUDGET"
                and isinstance(context.current_value, MoneyValue)
                and isinstance(context.request.new_value, MoneyValue)
                else 0
            )
            if own > row["reserved_increase_minor"] or (own > 0 and own != expected_hold):
                return check(
                    self.name,
                    False,
                    "本申请预占与冻结变更不一致",
                    {"reason": "OWN_HOLD_INCONSISTENT"},
                )
            if own:
                credited.append({"ledgerId": str(row["id"]), "heldMinor": own})
            result = amount_limit(
                context.request.action_type,
                context.current_value,
                context.request.new_value,
                confirmed=row["confirmed_total_minor"],
                reserved=row["reserved_increase_minor"] - own,
                limit=row["limit_minor"],
                currency=row["currency"],
                covered_paused=row["coverage_kind"] == "ALL_CONFIGURED_INCLUDING_PAUSED",
            )
            if not result.passed:
                return check(
                    self.name,
                    False,
                    "本次额度超限或预算基数不完整",
                    {
                        "reason": result.reason,
                        "ledgerId": str(row["id"]),
                        "ledgerVersion": row["version"],
                    },
                )
        return check(
            self.name,
            True,
            "预算基数与本次额度已核验",
            {
                "reason": "WITHIN_LIMIT",
                "ledgerIds": [str(row["id"]) for row in rows],
                "includesPaused": True,
                "ownHeldMinorByLedger": credited,
            },
        )


class ChangeBoundRule:
    name = "CHANGE_BOUND"
    stages = STAGES

    def evaluate(self, session: Session, context: RiskContext) -> CheckEvidence:
        if context.config is None:
            return check(
                self.name, False, "需要有效的变更策略", {"reason": "POLICY_NOT_CONFIGURED"}
            )
        result = change_bound(
            context.request.action_type,
            context.current_value,
            context.request.new_value,
            context.config.max_change_percent,
        )
        return check(
            self.name,
            result.passed,
            "核对本次金额、幅度与数值类型",
            {"reason": result.reason, "maxChangePercent": str(context.config.max_change_percent)},
        )


class FrequencyRule:
    name = "FREQUENCY"
    stages = STAGES

    def evaluate(self, session: Session, context: RiskContext) -> CheckEvidence:
        if context.config is None:
            return check(
                self.name, False, "需要有效的频率策略", {"reason": "POLICY_NOT_CONFIGURED"}
            )
        actor = context.identity
        try:
            snapshot = session.execute(
                text("""SELECT access.risk_action_summary(:member,:user,:job,
            :shop,:account,:target,:stage,:at)"""),
                {
                    "member": actor.membership_id,
                    "user": actor.user_id,
                    "job": context.job_id,
                    "shop": context.request.shop_id,
                    "account": context.target["account_id"],
                    "target": context.request.target_id,
                    "stage": context.stage.value,
                    "at": context.checked_at,
                },
            ).scalar_one()
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "42501":
                raise ApiError(ErrorCode.FORBIDDEN, "没有当前动作的核验范围") from None
            raise
        last = (
            datetime.fromisoformat(snapshot["lastVerifiedAt"])
            if snapshot["lastVerifiedAt"]
            else None
        )
        result = frequency_bound(
            at=context.checked_at,
            last_verified_at=last,
            verified_today=snapshot["verifiedToday"],
            unresolved_writes=snapshot["unresolvedWrites"],
            cooldown_minutes=context.config.cooldown_minutes,
            maximum_per_day=context.config.max_changes_per_day,
        )
        return check(
            self.name,
            result.passed,
            "核对已核验成功记录的冷却与日次数",
            {"reason": result.reason} | snapshot,
        )


def deterministic_rules(
    key: SecretStr,
) -> tuple[AmountLimitRule, ChangeBoundRule, FrequencyRule, "IntentBindingRule"]:
    from yuliang.modules.policy.intent import IntentBindingRule

    return AmountLimitRule(), ChangeBoundRule(), FrequencyRule(), IntentBindingRule(key)
