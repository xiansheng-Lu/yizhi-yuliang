"""V2冻结申请语义，不把生命周期版本或新的抓取时间放入原批准意图。"""

import hmac
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.command.jobs import CommandJob
from yuliang.modules.connectors.redaction import canonical, fingerprint
from yuliang.modules.policy.models import CheckEvidence, RiskContext, RiskStage
from yuliang.modules.policy.service import check

LIFECYCLE_FIELDS = frozenset(
    {
        "status",
        "version",
        "updated_at",
        "risk_summary",
        "approval",
        "execution_receipt",
        "error",
        "history",
    }
)


def seal_intent(
    key: SecretStr, job: CommandJob, requester: UUID, original_proof: dict[str, Any]
) -> str:
    payload = job.model_dump(mode="json", exclude=set(LIFECYCLE_FIELDS))
    payload.update(
        requester=str(requester),
        originalProof=original_proof,
        policyVersion=job.risk_summary.policy_version,
    )
    return fingerprint(key, "job-intent-v2", canonical(payload))


class IntentBindingRule:
    name = "INTENT_BINDING"
    stages = frozenset(RiskStage)

    def __init__(self, key: SecretStr) -> None:
        self.key = key

    def evaluate(self, session: Session, context: RiskContext) -> CheckEvidence:
        if context.job is None:
            # 真实创建在同一事务内按本次检查的策略/现值/来源封存；客户端无hash输入。
            passed = context.stage == RiskStage.CREATE and context.current_value is not None
            reason = "SEAL_ATOMIC_CREATION" if passed else "MISSING_CREATE_BASIS"
        else:
            stored = context.stored
            passed = False
            if stored and isinstance(stored.get("target_proof"), dict):
                expected = seal_intent(
                    self.key, context.job, context.requester.user_id, stored["target_proof"]
                )
                frozen = stored.get("intent_hash")
                passed = isinstance(frozen, str) and hmac.compare_digest(frozen, expected)
            reason = "ORIGINAL_INTENT_MATCHED" if passed else "FROZEN_INTENT_MISMATCH"
        return check(self.name, passed, "核对冻结指令及原始来源证明", {"reason": reason})


def approval_current(session: Session, context: RiskContext) -> CheckEvidence:
    job = context.job
    valid = False
    if (
        job is not None
        and job.approval.state == "APPROVED"
        and job.approval.method == "MANUAL"
        and job.approval.actor_id is not None
        and job.approval.decided_at is not None
        and job.created_at <= job.approval.decided_at <= context.checked_at
        and job.approval.approved_version is not None
        and job.approval.approved_version < job.version
    ):
        valid = session.execute(
            text("SELECT access.job_approval_current(:member,:user,:job)"),
            {
                "member": context.identity.membership_id,
                "user": context.identity.user_id,
                "job": context.job_id,
            },
        ).scalar_one()
    return check(
        "APPROVAL_CURRENT", valid, "核对当前人工批准及审批人资格", {"approvalCurrent": valid}
    )
