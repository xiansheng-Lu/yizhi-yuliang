"""四时点均重新读取实际证据；未实现的必需规则按缺项拒绝。"""

import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.audit.service import record
from yuliang.modules.capability.models import CONNECTOR_CONTEXTS
from yuliang.modules.capability.service import evaluate_action
from yuliang.modules.command.jobs import (
    CommandJob,
    CreateJobRequest,
    JobValue,
    RiskCheck,
    RiskSummary,
)
from yuliang.modules.policy.models import (
    CheckEvidence,
    PolicyConfig,
    RiskContext,
    RiskDecision,
    RiskStage,
)
from yuliang.modules.policy.repository import policy_lock
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode

if TYPE_CHECKING:
    from yuliang.modules.command.applications import JobApplications
    from yuliang.modules.execution.guards import GuardLease

REQUIRED_RULES = frozenset({"AMOUNT_LIMIT", "CHANGE_BOUND", "FREQUENCY", "INTENT_BINDING"})


class RiskRule(Protocol):
    name: str
    stages: frozenset[RiskStage]

    def evaluate(self, session: Session, context: RiskContext) -> CheckEvidence: ...


def check(code: str, passed: bool, message: str, evidence: dict[str, Any]) -> CheckEvidence:
    return CheckEvidence(
        check=RiskCheck(code=code, passed=passed, message=message), evidence=evidence
    )


class RiskFramework:
    def __init__(
        self, applications: "JobApplications", rules: Iterable[RiskRule] | None = None
    ) -> None:
        if rules is None:
            from yuliang.modules.policy.rules import deterministic_rules

            rules = deterministic_rules(applications.key)
        self.applications = applications
        implementations = tuple(rules)
        self.rules = {rule.name: rule for rule in implementations}
        if len(implementations) != len(self.rules) or not self.rules.keys() <= REQUIRED_RULES:
            raise ValueError("规则必须来自明确的服务端实现清单")

    def assess_create(
        self,
        session: Session,
        value: CreateJobRequest,
        target: dict[str, Any],
        old: JobValue | None,
        proof: dict[str, Any],
        job_id: UUID,
        at: datetime,
    ) -> RiskDecision:
        # 创建入口重新读对象范围与确认页，不能让调用方提供的旧值/归属代替事实。
        actual = self.applications.target(session, value)
        fresh, actual_proof = self.applications.current_value(session, value, actual)
        if actual["id"] != target["id"] or old != fresh or proof != actual_proof:
            raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "申请现值需要重新核对")
        return self._assess(
            session,
            value,
            actual,
            fresh,
            actual_proof,
            job_id,
            RiskStage.CREATE,
            self.applications.identity,
            None,
            at,
        )

    def assess_job(
        self,
        session: Session,
        job_id: UUID,
        stage: RiskStage,
        *,
        at: datetime | None = None,
        expected_version: int | None = None,
    ) -> RiskDecision:
        return self._assess_job(session, job_id, stage, at=at, expected_version=expected_version)

    def assess_dispatch(self, session: Session, lease: "GuardLease") -> RiskDecision:
        from yuliang.modules.execution.guards import TargetGuards

        TargetGuards(self.applications).assert_current(session, lease)
        return self._assess_job(
            session, lease.jobId, RiskStage.EXECUTE, expected_version=lease.jobVersion, claimed=True
        )

    def _assess_job(
        self,
        session: Session,
        job_id: UUID,
        stage: RiskStage,
        *,
        at: datetime | None = None,
        expected_version: int | None = None,
        claimed: bool = False,
    ) -> RiskDecision:
        if stage not in {RiskStage.CONFIRM, RiskStage.APPROVE, RiskStage.EXECUTE}:
            raise ValueError("既有申请需要明确的生命周期检查时点")
        event = {
            RiskStage.CONFIRM: "CONFIRM",
            RiskStage.APPROVE: "APPROVE",
            RiskStage.EXECUTE: "CLAIM",
        }[stage]
        identity = self.applications.identity
        try:
            row = dict(
                session.execute(
                    text("SELECT access.job_state_context(:member,:user,:job,:event)"),
                    {
                        "member": identity.membership_id,
                        "user": identity.user_id,
                        "job": job_id,
                        "event": event,
                    },
                ).scalar_one()
            )
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None)
            if code in {"42501", "P0002"}:
                raise ApiError(
                    ErrorCode.FORBIDDEN if code == "42501" else ErrorCode.NO_DATA,
                    "当前范围内的任务不可核验",
                ) from None
            raise
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 1
        ):
            raise ApiError(ErrorCode.INVALID_INPUT, "需要有效申请版本")
        if expected_version is not None and row["version"] != expected_version:
            raise ApiError(ErrorCode.VERSION_CONFLICT, "申请版本已变化")
        job = CommandJob.model_validate(row["document"])
        value = CreateJobRequest.model_validate(
            {key: row["document"][key] for key in CreateJobRequest.model_fields}
        )
        policy_lock(session, identity.tenant_id, shared=True)
        requester_member = session.execute(
            text("SELECT id FROM memberships WHERE tenant_id=:tenant AND user_id=:user"),
            {"tenant": identity.tenant_id, "user": UUID(row["created_by"])},
        ).scalar_one()
        requester = VerifiedIdentity(identity.tenant_id, requester_member, UUID(row["created_by"]))
        query = (
            "SELECT * FROM campaigns WHERE id=:id AND shop_id=:shop"
            if value.target_type == "AD_PLAN"
            else "SELECT * FROM live_sessions WHERE id=:id AND shop_id=:shop"
        )
        target = (
            session.execute(text(query), {"id": value.target_id, "shop": value.shop_id})
            .mappings()
            .first()
        )
        if target is None:
            raise ApiError(ErrorCode.NO_DATA, "当前范围内没有可核验目标")
        error_code = None
        try:
            old, proof = self.applications.current_value(session, value, dict(target))
        except ApiError as error:
            if error.code not in {ErrorCode.FORBIDDEN, ErrorCode.DATA_COVERAGE_INSUFFICIENT}:
                raise
            old, proof, error_code = None, {}, error.code.value
        stamp = at or session.execute(text("SELECT clock_timestamp()")).scalar_one()
        return self._assess(
            session,
            value,
            dict(target),
            old,
            proof,
            job_id,
            stage,
            requester,
            job,
            stamp,
            current_error=error_code,
            stored=row,
            claimed=claimed,
        )

    def require_job(
        self,
        session: Session,
        job_id: UUID,
        stage: RiskStage,
        expected_version: int,
        *,
        at: datetime | None = None,
    ) -> RiskDecision:
        decision = self.assess_job(session, job_id, stage, expected_version=expected_version, at=at)
        decision.require_allowed()
        return decision

    def _assess(
        self,
        session: Session,
        value: CreateJobRequest,
        target: dict[str, Any],
        current: JobValue | None,
        proof: dict[str, Any],
        job_id: UUID,
        stage: RiskStage,
        requester: VerifiedIdentity,
        job: CommandJob | None,
        at: datetime,
        *,
        current_error: str | None = None,
        stored: dict[str, Any] | None = None,
        claimed: bool = False,
    ) -> RiskDecision:
        from yuliang.modules.command.applications import ACTION_CAPABILITIES

        identity = self.applications.identity
        policy_lock(session, identity.tenant_id, shared=True)
        policy = (
            session.execute(
                text("""SELECT p.id,p.version,v.config FROM risk_policies p
        JOIN policy_versions v ON (v.tenant_id,v.policy_id,v.version)=(p.tenant_id,p.id,p.version)
        WHERE p.tenant_id=:tenant"""),
                {"tenant": identity.tenant_id},
            )
            .mappings()
            .first()
        )
        config = None
        if policy:
            try:
                config = PolicyConfig.model_validate(policy["config"])
            except ValidationError:
                config = None
        policy_id, version = (policy["id"], policy["version"]) if policy else (None, 0)
        manual = value.target_type == "LIVE_SESSION"
        expected = {
            RiskStage.CONFIRM: "DRAFT",
            RiskStage.APPROVE: "PENDING_APPROVAL",
            RiskStage.EXECUTE: "EXECUTING" if claimed else "APPROVED",
        }
        checks = [
            check("TARGET_SCOPE", True, "已重新核对当前动作与目标范围", {"stage": stage.value}),
            check(
                "POLICY_CONFIGURED",
                config is not None,
                "核对有效策略配置",
                {"policyId": str(policy_id) if policy_id else None, "version": version},
            ),
            check(
                "POLICY_VERSION",
                job is None or job.risk_summary.policy_version == version,
                "核对申请与当前策略版本",
                {"expected": job.risk_summary.policy_version if job else None, "current": version},
            ),
            check(
                "STAGE_STATUS",
                job is None or job.status.value == expected.get(stage),
                "核对当前申请阶段",
                {"stage": stage.value, "status": job.status.value if job else "CREATE"},
            ),
            check(
                "JOB_WINDOW",
                job is None or job.expires_at > at,
                "核对申请有效期",
                {"expired": job is not None and job.expires_at <= at},
            ),
        ]
        if manual:
            checks.append(
                check(
                    "STREAM_CONTROL_UNAVAILABLE",
                    False,
                    "该直播控制需人工交接",
                    {"integratedStreamControl": False},
                )
            )
        else:
            permission = ACTION_CAPABILITIES[value.action_type][1]
            authorized = True
            try:
                for code in ("task.create", permission):
                    require_shop_account(
                        session, requester, value.shop_id, target["account_id"], code
                    )
            except ApiError as error:
                if error.code != ErrorCode.FORBIDDEN:
                    raise
                authorized = False
            connector = next(
                key
                for key, pair in CONNECTOR_CONTEXTS.items()
                if pair == (target["provider"], target["product_line"])
            )
            cap = evaluate_action(
                session,
                requester,
                value.shop_id,
                target["account_id"],
                connector,
                target["product_line"],
                ACTION_CAPABILITIES[value.action_type][0],
            )
            writable = cap["status"] == "AUTHORIZED" and any(
                op["kind"] == "WRITE" for op in cap["operations"]
            )
            checks.extend(
                [
                    check(
                        "REQUESTER_AUTHORIZATION",
                        authorized,
                        "核对申请人当前动作授权",
                        {"permission": permission, "authorized": authorized},
                    ),
                    check(
                        "ACTION_CAPABILITY",
                        writable,
                        "核对该动作当前能力证据",
                        {"status": cap["status"], "writableOperation": writable},
                    ),
                    check(
                        "CURRENT_VALUE_CONFIRMED",
                        current is not None and current_error is None,
                        "核对当前已确认且有效的数据",
                        proof | {"errorCode": current_error},
                    ),
                    check(
                        "CURRENT_VALUE_UNCHANGED",
                        job is None or current == job.old_value,
                        "核对平台现值与原申请是否一致",
                        {"unchanged": job is None or current == job.old_value},
                    ),
                    check(
                        "CURRENT_VALUE_CONTRACT",
                        job is None
                        or (
                            stored is not None
                            and all(
                                proof.get(key) == stored["target_proof"].get(key)
                                for key in ("sourceId", "field", "policyRevision")
                            )
                        ),
                        "核对批准前后稳定的数据来源合同",
                        {
                            "contractMatched": job is None
                            or (
                                stored is not None
                                and all(
                                    proof.get(key) == stored["target_proof"].get(key)
                                    for key in ("sourceId", "field", "policyRevision")
                                )
                            )
                        },
                    ),
                ]
            )
        context = RiskContext(
            identity=identity,
            requester=requester,
            job_id=job_id,
            job=job,
            request=value,
            target=target,
            current_value=current,
            current_proof=proof,
            stage=stage,
            policy_id=policy_id,
            policy_version=version,
            config=config,
            checked_at=at,
            stored=stored,
        )
        installed = set()
        if stage == RiskStage.EXECUTE and not manual:
            from yuliang.modules.policy.intent import approval_current

            checks.append(approval_current(session, context))
        if not manual:
            for name in sorted(REQUIRED_RULES):
                rule = self.rules.get(name)
                if rule is None or stage not in rule.stages:
                    checks.append(
                        check(
                            name,
                            False,
                            "必要风险依据尚未就绪，暂不能执行",
                            {"rule": name, "reason": "RULE_NOT_INSTALLED"},
                        )
                    )
                else:
                    outcome = rule.evaluate(session, context)
                    if outcome.check.code != name:
                        raise ValueError("规则实现须返回与其登记一致的检查码")
                    checks.append(outcome)
                    installed.add(name)
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
        expires = at + timedelta(seconds=10)
        checks.append(
            check(
                "ASSESSMENT_WINDOW",
                now < expires,
                "核对本次检查仍在有效窗口",
                {"checkedAt": at.isoformat(), "expiresAt": expires.isoformat()},
            )
        )
        checks.append(
            check(
                "POLICY_ENGINE_READY",
                config is not None and installed == REQUIRED_RULES,
                "必要风险检查尚未全部就绪时保持阻断",
                {"installed": sorted(installed), "required": sorted(REQUIRED_RULES)},
            )
        )
        result = (
            "MANUAL_ONLY"
            if manual
            else "REVIEW_REQUIRED"
            if all(c.check.passed for c in checks)
            else "BLOCKED"
        )
        summary = RiskSummary(
            result=result,
            level="HIGH",
            checks=tuple(item.check for item in checks),
            policy_version=version,
            checked_at=at,
        )
        decision = RiskDecision(assessment_id=uuid4(), summary=summary, expires_at=expires)
        session.execute(
            text("""INSERT INTO risk_assessments(id,tenant_id,job_id,user_id,membership_id,
        request_id,stage,job_version,policy_id,policy_version,checked_at,expires_at,summary,evidence)
        VALUES(:id,:tenant,:job,:user,:member,:request,:stage,:job_version,:policy,:version,
        :checked,:expires,CAST(:summary AS jsonb),CAST(:evidence AS jsonb))"""),
            {
                "id": decision.assessment_id,
                "tenant": identity.tenant_id,
                "job": job_id,
                "user": identity.user_id,
                "member": identity.membership_id,
                "request": self.applications.request_id,
                "stage": stage.value,
                "job_version": job.version if job else None,
                "policy": policy_id,
                "version": version,
                "checked": at,
                "expires": expires,
                "summary": summary.model_dump_json(),
                "evidence": json.dumps([item.model_dump(mode="json") for item in checks]),
            },
        )
        record(
            session,
            identity,
            {
                "action": "risk.assess",
                "object_type": "job",
                "object_id": job_id,
                "request_id": self.applications.request_id,
            },
        )
        return decision
