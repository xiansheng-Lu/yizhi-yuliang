"""维护核验导入：只接受有明确受理和独立回读原文的规范DTO；不提供HTTP入口。"""

import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from yuliang.modules.capability.service import maintenance
from yuliang.modules.command.applications import ACTION_CAPABILITIES
from yuliang.modules.command.jobs import BooleanValue, CommandJob, JobValue, MoneyValue, RatioValue


def record_verified_action(
    connection: Connection, job_id: UUID, write_id: UUID, read_id: UUID, proof_ref: str
) -> UUID:
    """真实执行Writer归M2-17；本入口仅独立维护身份用于可核对导入/技术验证。"""
    maintenance(connection)
    if write_id == read_id or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,180}", proof_ref):
        raise ValueError("需要独立回读与有效证明引用")
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760209))"), {"key": str(write_id)}
    )
    existing = (
        connection.execute(
            text("SELECT * FROM verified_action_facts WHERE write_outbound_id=:id"),
            {"id": write_id},
        )
        .mappings()
        .first()
    )
    if existing:
        if (existing["job_id"], existing["read_outbound_id"], existing["proof_ref"]) != (
            job_id,
            read_id,
            proof_ref,
        ):
            raise ValueError("原核验事实不能替换任务、回读或引用")
        return UUID(str(existing["id"]))
    stored = (
        connection.execute(
            text("""SELECT j.*,c.native_id,c.provider,c.product_line
    FROM command_jobs j JOIN campaigns c ON (c.tenant_id,c.id)=(j.tenant_id,j.campaign_id)
    WHERE j.id=:id"""),
            {"id": job_id},
        )
        .mappings()
        .one()
    )
    job = CommandJob.model_validate(stored["document"])
    mapping = (
        connection.execute(
            text("""SELECT m.*,s.binding_id,p.clock_kind,p.max_age_seconds,p.revision
    FROM command_value_sources m JOIN sync_sources s ON (s.tenant_id,s.id)=(m.tenant_id,m.source_id)
    JOIN resource_freshness_policies p ON (p.tenant_id,p.source_id)=(m.tenant_id,m.source_id)
    WHERE m.tenant_id=:tenant AND m.campaign_id=:campaign AND m.action_type=:action"""),
            {
                "tenant": stored["tenant_id"],
                "campaign": stored["campaign_id"],
                "action": job.action_type,
            },
        )
        .mappings()
        .one()
    )
    if (stored["target_proof"].get("sourceId"), stored["target_proof"].get("policyRevision")) != (
        str(mapping["source_id"]),
        mapping["revision"],
    ):
        raise ValueError("核验字段合同已变化，不能借用另一个来源")
    write = (
        connection.execute(text("SELECT * FROM platform_requests WHERE id=:id"), {"id": write_id})
        .mappings()
        .one()
    )
    read = (
        connection.execute(text("SELECT * FROM platform_requests WHERE id=:id"), {"id": read_id})
        .mappings()
        .one()
    )
    expected = (
        stored["tenant_id"],
        stored["account_id"],
        stored["provider"],
        stored["product_line"],
        stored["native_id"],
    )
    for root in (write, read):
        if (
            root["tenant_id"],
            root["account_id"],
            root["provider"],
            root["product_line"],
            root["target_native_id"],
        ) != expected:
            raise ValueError("原请求和回读的完整目标上下文不一致")
    write_binding = connection.execute(
        text("SELECT action,operation_kind FROM capability_bindings WHERE id=:id"),
        {"id": write["binding_id"]},
    ).one()
    if (
        write["effect"],
        read["effect"],
        read["binding_id"],
        write["user_id"],
        tuple(write_binding),
    ) != (
        "WRITE",
        "READ",
        mapping["binding_id"],
        stored["created_by"],
        (ACTION_CAPABILITIES[job.action_type][0], "WRITE"),
    ):
        raise ValueError("受理、回读或原申请人上下文不匹配")

    def outcome(outbound: UUID, kind: str) -> RowMapping | None:
        return (
            connection.execute(
                text("""SELECT * FROM platform_raw_payloads WHERE outbound_id=:id
        AND phase='RESPONSE' AND external_outcome=:kind ORDER BY id DESC LIMIT 1"""),
                {"id": outbound, "kind": kind},
            )
            .mappings()
            .first()
        )

    accepted, observed = outcome(write_id, "ACCEPTED"), outcome(read_id, "READ_SUCCEEDED")
    if accepted is None or observed is None or observed["created_at"] < accepted["created_at"]:
        raise ValueError("HTTP成功不等于已受理和独立核验")
    body = observed["payload"].get("data")
    payload = body.get("data") if isinstance(body, dict) else None
    request = write["request_json"].get("data", {})
    if (
        not isinstance(payload, dict)
        or not isinstance(request, dict)
        or payload.get("id") != stored["native_id"]
    ):
        raise ValueError("规范回读DTO没有明确的目标ID")
    value = payload.get(mapping["value_field"])
    new = job.new_value
    scalar = new.amount_minor if isinstance(new, MoneyValue) else new.value
    if (
        type(request.get(mapping["value_field"])) is not type(scalar)
        or request.get(mapping["value_field"]) != scalar
    ):
        raise ValueError("发送参数不是该申请的冻结变更值")
    if isinstance(new, MoneyValue):
        if payload.get("currency") != "CNY" or request.get("currency") != "CNY":
            raise ValueError("币种缺失或不一致，不能推定CNY")
        parsed: JobValue = MoneyValue.model_validate({"amount_minor": value})
    elif isinstance(new, RatioValue):
        parsed = RatioValue.model_validate({"value": value})
    elif isinstance(new, BooleanValue):
        parsed = BooleanValue.model_validate({"value": value})
    else:
        raise ValueError("人工交接不能登记平台执行成功")
    if parsed != new:
        raise ValueError("MISMATCH不能登记为成功事实")
    if mapping["clock_kind"] == "SOURCE":
        source = payload.get("source_updated_at")
        if not isinstance(source, str):
            raise ValueError("来源时间未知，不能证明受理后的状态")
        try:
            stamp = datetime.fromisoformat(source)
        except ValueError:
            raise ValueError("来源时钟格式未核验") from None
        if stamp.tzinfo is None or not accepted["created_at"] <= stamp <= observed["created_at"]:
            raise ValueError("来源时钟不能证明受理后的状态")
        if observed["created_at"] - stamp > timedelta(seconds=mapping["max_age_seconds"]):
            raise ValueError("回读来源已经过期，不能登记为成功")
    fact_id = uuid4()
    verified_at = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    connection.execute(
        text("""INSERT INTO verified_action_facts(id,tenant_id,job_id,account_id,
    campaign_id,provider,product_line,action_type,write_outbound_id,read_outbound_id,
    write_payload_id,read_payload_id,observed_value,verified_at,proof_ref)
    VALUES(:id,:tenant,:job,:account,:campaign,:provider,:product,:action,:write,:read,
    :write_payload,:read_payload,CAST(:value AS jsonb),:at,:proof)"""),
        {
            "id": fact_id,
            "tenant": stored["tenant_id"],
            "job": job_id,
            "account": stored["account_id"],
            "campaign": stored["campaign_id"],
            "provider": stored["provider"],
            "product": stored["product_line"],
            "action": job.action_type,
            "write": write_id,
            "read": read_id,
            "write_payload": accepted["id"],
            "read_payload": observed["id"],
            "value": parsed.model_dump_json(),
            "at": verified_at,
            "proof": proof_ref,
        },
    )
    return fact_id
