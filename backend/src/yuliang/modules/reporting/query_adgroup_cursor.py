"""ADGROUP有界私有分页位置；随机ID只定位，不承担授权。"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.reporting.query_models import QueryRequest, ResourcePage
from yuliang.platform.errors import ApiError, ErrorCode

PAGE_SIZE = 100
MAX_TOTAL = 10000
MAX_CHAINS = 32
MAX_PRIVATE_ROWS = 3200
TTL_SECONDS = 600
PREFIX = "adgroup-c1."


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def offsets(total: int) -> list[int]:
    if type(total) is not int or not 0 <= total <= MAX_TOTAL:
        raise ApiError(
            ErrorCode.DATA_COVERAGE_INSUFFICIENT, "完整广告组来源超过10000条，不返回截断分页"
        )
    return list(range(PAGE_SIZE, total, PAGE_SIZE))


def selector(identifier: UUID | None) -> str | None:
    return PREFIX + identifier.hex if identifier is not None else None


def parse_selector(value: str) -> UUID:
    try:
        if not value.startswith(PREFIX) or len(value) != len(PREFIX) + 32:
            raise ValueError("shape")
        identifier = UUID(hex=value[len(PREFIX) :])
        if selector(identifier) != value:
            raise ValueError("canonical")
        return identifier
    except ValueError:
        raise ApiError(ErrorCode.INVALID_INPUT, "分页游标无效或已过期，请重新查询") from None


def request_identity(ctx: GatewayContext, value: QueryRequest) -> dict[str, Any]:
    return dict(
        tenant=ctx.identity.tenant_id,
        user=str(ctx.identity.user_id),
        member=str(ctx.identity.membership_id),
        resource="adgroups",
        level="ADGROUP",
        scope=value.scope.key(),
        search=(value.filters.search or "").strip().casefold(),
        parent=value.filters.native_parent_id or "",
        sort="NATIVE_INT64_ASC",
        size=PAGE_SIZE,
    )


def permissions_fingerprint(s: Session, ctx: GatewayContext) -> str:
    args = dict(member=ctx.identity.membership_id, user=ctx.identity.user_id)
    membership = (
        s.execute(
            text("SELECT status,authz_version FROM memberships WHERE id=:member AND user_id=:user"),
            args,
        )
        .mappings()
        .one()
    )
    grants = list(
        s.execute(
            text(
                "SELECT g.role_id,r.version,p.permission_code FROM role_grants g "
                "JOIN roles r ON (r.tenant_id,r.id)=(g.tenant_id,g.role_id) "
                "JOIN role_permissions p ON (p.tenant_id,p.role_id)=(r.tenant_id,r.id) "
                "WHERE g.membership_id=:member ORDER BY g.role_id,p.permission_code"
            ),
            args,
        ).mappings()
    )
    scopes = list(
        s.execute(
            text(
                "SELECT scope_type,entity_id,shop_id,account_id,permissions_mask FROM data_scopes "
                "WHERE membership_id=:member ORDER BY id"
            ),
            args,
        ).mappings()
    )
    return digest(
        dict(
            member=dict(membership),
            grants=[dict(x) for x in grants],
            scopes=[dict(x) for x in scopes],
        )
    )


def paginate(
    s: Session,
    ctx: GatewayContext,
    value: QueryRequest,
    page: ResourcePage,
    snapshot: dict[str, Any],
) -> ResourcePage:
    total = len(page.rows)
    positions = offsets(total)
    cursor = value.filters.cursor
    if not cursor and not positions:
        return page
    now = s.execute(text("SELECT clock_timestamp()")).scalar_one()
    identity = request_identity(ctx, value)
    request_hash = digest(identity)
    snapshot_hash = digest(
        dict(
            source=snapshot,
            permissions=permissions_fingerprint(s, ctx),
            page=page.model_dump(mode="json"),
        )
    )
    # RR下并发同actor写此行触发40001，由QueryService有界重跑整个scope/source快照。
    s.execute(
        text(
            "INSERT INTO resource_query_cursor_owners(tenant_id,membership_id,user_id,generation) "
            "VALUES(:tenant,:member,:user,1) ON CONFLICT(tenant_id,membership_id,user_id) "
            "DO UPDATE SET generation=resource_query_cursor_owners.generation+1,"
            "touched_at=clock_timestamp()"
        ),
        identity,
    )
    if cursor:
        row = (
            s.execute(
                text("SELECT * FROM resource_query_continuations WHERE id=:id"),
                dict(id=parse_selector(cursor)),
            )
            .mappings()
            .first()
        )
        if row is None or row["expires_at"] <= now:
            raise ApiError(ErrorCode.VERSION_CONFLICT, "分页游标无效或已过期，请重新查询首屏")
        if (
            row["request_hash"] != request_hash
            or row["snapshot_hash"] != snapshot_hash
            or row["total_rows"] != total
        ):
            raise ApiError(
                ErrorCode.VERSION_CONFLICT, "筛选、权限或完整来源批次已改变，请重新查询首屏"
            )
        offset = row["after_offset"]
        if type(offset) is not int or offset not in positions:
            raise ApiError(ErrorCode.INVALID_INPUT, "分页位置非法")
        return page.model_copy(
            update=dict(
                rows=page.rows[offset : offset + PAGE_SIZE], next_cursor=selector(row["next_id"])
            )
        )
    s.execute(
        text(
            "DELETE FROM resource_query_continuations WHERE chain_id IN ("
            "SELECT chain_id FROM resource_query_continuations GROUP BY chain_id "
            "HAVING max(expires_at)<=clock_timestamp() ORDER BY max(expires_at),chain_id LIMIT 3)"
        )
    )
    chains = s.execute(
        text(
            "SELECT count(DISTINCT chain_id) FROM resource_query_continuations "
            "WHERE expires_at>:now"
        ),
        dict(now=now),
    ).scalar_one()
    rows = s.execute(text("SELECT count(*) FROM resource_query_continuations")).scalar_one()
    if chains >= MAX_CHAINS or rows + len(positions) > MAX_PRIVATE_ROWS:
        raise ApiError(
            ErrorCode.RATE_LIMITED, "分页上下文已达当前身份上限，请复用已有游标或稍后重试"
        )
    expiry = now + timedelta(seconds=TTL_SECONDS)
    proof = snapshot.get("measureProof")
    if proof:
        proof_expiry = datetime.fromisoformat(proof["expiresAt"])
        if proof_expiry > now:
            expiry = min(expiry, proof_expiry)
    chain = uuid4()
    identifiers = [uuid4() for _ in positions]
    for index, offset in enumerate(positions):
        s.execute(
            text(
                "INSERT INTO resource_query_continuations("
                "id,tenant_id,membership_id,user_id,chain_id,"
                "source_id,run_id,request_hash,snapshot_hash,total_rows,"
                "after_offset,next_id,expires_at) "
                "VALUES(:id,:tenant,:member,:user,:chain,:source,:run,"
                ":request,:snapshot,:total,:offset,:next,:expiry)"
            ),
            dict(
                id=identifiers[index],
                tenant=ctx.identity.tenant_id,
                member=ctx.identity.membership_id,
                user=ctx.identity.user_id,
                chain=chain,
                source=UUID(snapshot["sourceId"]),
                run=UUID(snapshot["runId"]),
                request=request_hash,
                snapshot=snapshot_hash,
                total=total,
                offset=offset,
                next=identifiers[index + 1] if index + 1 < len(identifiers) else None,
                expiry=expiry,
            ),
        )
    return page.model_copy(
        update=dict(rows=page.rows[:PAGE_SIZE], next_cursor=selector(identifiers[0]))
    )
