"""维护策略只追加版本；运行身份只能读取，不能自行提额或覆盖历史。"""

import json
import re
from uuid import UUID, uuid4

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from yuliang.modules.capability.service import maintenance
from yuliang.modules.policy.models import PolicyConfig


def policy_lock(connection: Connection | Session, tenant: int, *, shared: bool) -> None:
    query = (
        "SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,760208))"
        if shared
        else "SELECT pg_advisory_xact_lock(hashtextextended(:key,760208))"
    )
    connection.execute(text(query), {"key": str(tenant)})


def install_policy(
    connection: Connection,
    tenant: int,
    config: PolicyConfig,
    *,
    expected_version: int,
    proof_ref: str,
) -> tuple[UUID, int]:
    maintenance(connection)
    if (
        type(expected_version) is not int
        or expected_version < 0
        or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,180}", proof_ref)
    ):
        raise ValueError("策略维护需要期望版本与可核对的来源")
    policy_lock(connection, tenant, shared=False)
    row = (
        connection.execute(
            text("SELECT id,version FROM risk_policies WHERE tenant_id=:tenant"), {"tenant": tenant}
        )
        .mappings()
        .first()
    )
    if (row["version"] if row else 0) != expected_version:
        raise ValueError("策略版本已经变化")
    policy_id, version = (row["id"] if row else uuid4()), expected_version + 1
    connection.execute(
        text("""INSERT INTO risk_policies(id,tenant_id,version) VALUES(:id,:tenant,:version)
        ON CONFLICT(tenant_id) DO UPDATE SET version=EXCLUDED.version"""),
        {"id": policy_id, "tenant": tenant, "version": version},
    )
    connection.execute(
        text("""INSERT INTO policy_versions(tenant_id,policy_id,version,config,proof_ref)
        VALUES(:tenant,:id,:version,CAST(:config AS jsonb),:proof)"""),
        {
            "tenant": tenant,
            "id": policy_id,
            "version": version,
            "config": json.dumps(config.model_dump(mode="json")),
            "proof": proof_ref,
        },
    )
    return policy_id, version
