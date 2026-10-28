"""真实同步/PG/会话HTTP分页；平台仅合成内存传输，无真实平台调用。"""

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.platform.db import business_transaction

from .test_resource_query_plans import (
    assets as assets,
)
from .test_resource_query_plans import (
    capability as capability,
)
from .test_resource_query_plans import (
    configuration_ready as configuration_ready,
)
from .test_resource_query_plans import (
    database as database,
)
from .test_resource_query_plans import (
    governance as governance,
)
from .test_resource_query_plans import (
    http,
    proof,
    request,
)
from .test_resource_query_plans import (
    legacy as legacy,
)
from .test_resource_query_plans import (
    login_api as login_api,
)
from .test_resource_query_plans import (
    oauth_ready as oauth_ready,
)
from .test_resource_query_plans import (
    query_plans as query_plans,
)

pytestmark = pytest.mark.integration


async def seed(d: dict[str, Any], total: int = 1101) -> Any:
    template = d["state"]["rows"][0]
    d["state"]["rows"] = [
        template | dict(adgroup_id=i, adgroup_name=f"合成组{i}", campaign_id=10)
        for i in range(total, 0, -1)
    ]
    await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=100)
    return request(d, d["ctx"], "adgroups")


def following(value: Any, cursor: str) -> Any:
    return value.model_copy(
        update=dict(filters=value.filters.model_copy(update=dict(cursor=cursor)))
    )


def ok(d: dict[str, Any], value: Any) -> dict[str, Any]:
    response = http(d, value)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_actual_http_complete_chain_replay_and_consumer_capture(
    query_plans: dict[str, Any],
) -> None:
    d = query_plans
    value = await seed(d)
    count = len(d["calls"])
    first = ok(d, value)
    assert len(first["rows"]) == 100
    cursor = first["nextCursor"]
    second_input = following(value, cursor)
    second = ok(d, second_input)
    assert ok(d, second_input) == second
    pages = [dict(input=second_input.model_dump(mode="json", by_alias=True), page=second)]
    rows = first["rows"] + second["rows"]
    page = second
    while page["nextCursor"]:
        current = following(value, page["nextCursor"])
        page = ok(d, current)
        assert 0 < len(page["rows"]) <= 100
        assert page["syncedAt"] == first["syncedAt"]
        rows.extend(page["rows"])
        pages.append(dict(input=current.model_dump(mode="json", by_alias=True), page=page))
    assert [r["nativeId"] for r in rows] == [str(i) for i in range(1, 1102)]
    assert len(d["calls"]) == count
    if destination := os.getenv("YULIANG_ADGROUP_PAGINATION_CAPTURE"):
        Path(destination).write_text(
            json.dumps(
                dict(
                    primary=dict(input=value.model_dump(mode="json", by_alias=True), page=first),
                    pages=pages,
                    replay=dict(
                        input=second_input.model_dump(mode="json", by_alias=True),
                        page=ok(d, second_input),
                    ),
                    context=d["client"]
                    .get("/v1/delivery/integrations/context?productLine=adq")
                    .json()["data"],
                    identity=d["client"].get("/v1/auth/session").json()["data"],
                    expectedNativeIds=[str(i) for i in range(1, 1102)],
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


@pytest.mark.parametrize("change", ["search", "parent", "date", "malformed"])
async def test_cursor_is_bound_to_complete_request(
    query_plans: dict[str, Any], change: str
) -> None:
    d = query_plans
    value = await seed(d)
    continuation = following(value, ok(d, value)["nextCursor"])
    if change == "date":
        continuation = continuation.model_copy(
            update=dict(scope=continuation.scope.model_copy(update=dict(end="2026-10-27")))
        )
    else:
        fields = {
            "search": dict(search="合成"),
            "parent": dict(native_parent_id="20"),
            "malformed": dict(cursor="bad"),
        }[change]
        continuation = continuation.model_copy(
            update=dict(filters=continuation.filters.model_copy(update=fields))
        )
    assert http(d, continuation).status_code in (409, 422)


@pytest.mark.parametrize("change", ["new_run", "failed_run", "proof", "permission", "expiry"])
async def test_current_source_permission_and_expiry_are_rechecked(
    query_plans: dict[str, Any], change: str
) -> None:
    d = query_plans
    value = await seed(d)
    continuation = following(value, ok(d, value)["nextCursor"])
    if change in {"new_run", "failed_run"}:
        d["state"]["fail"] = change == "failed_run"
        try:
            await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=100)
        except Exception:
            if change != "failed_run":
                raise
    elif change == "proof":
        proof(d)
    else:
        with d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            if change == "permission":
                owner.execute(
                    text("UPDATE memberships SET authz_version=authz_version+1 WHERE id=:id"),
                    dict(id=d["identity"].membership_id),
                )
            else:
                owner.execute(
                    text(
                        "UPDATE resource_query_continuations "
                        "SET expires_at=clock_timestamp()-interval '1 second'"
                    )
                )
    assert http(d, continuation).status_code in (403, 409, 422)


async def test_cleanup_deletes_whole_three_expired_chains_over_256_rows(
    query_plans: dict[str, Any],
) -> None:
    d = query_plans
    value = await seed(d, 10000)
    for _ in range(3):
        assert ok(d, value)["nextCursor"]
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            owner.execute(
                text("SELECT count(*) FROM resource_query_continuations WHERE tenant_id=:tenant"),
                dict(tenant=d["tenant"]),
            ).scalar_one()
            == 297
        )
        owner.execute(
            text(
                "UPDATE resource_query_continuations "
                "SET expires_at=clock_timestamp()-interval '1 second'"
            )
        )
    assert ok(d, value)["nextCursor"]
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            owner.execute(
                text("SELECT count(*) FROM resource_query_continuations WHERE tenant_id=:tenant"),
                dict(tenant=d["tenant"]),
            ).scalar_one()
            == 99
        )


async def test_active_context_limit_is_bounded_but_replay_survives(
    query_plans: dict[str, Any],
) -> None:
    d = query_plans
    value = await seed(d, 101)
    first = ok(d, value)
    for _ in range(31):
        ok(d, value)
    assert http(d, value).status_code == 429
    assert len(ok(d, following(value, first["nextCursor"]))["rows"]) == 1


async def test_known_other_actor_locator_cannot_be_read_or_linked(
    query_plans: dict[str, Any],
) -> None:
    d = query_plans
    value = await seed(d)
    ok(d, value)
    user, member, other, chain = uuid4(), uuid4(), uuid4(), uuid4()
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        original = dict(
            owner.execute(
                text("SELECT * FROM resource_query_continuations WHERE tenant_id=:tenant LIMIT 1"),
                dict(tenant=d["tenant"]),
            )
            .mappings()
            .one()
        )
        owner.execute(
            text("INSERT INTO users(id,email,display_name) VALUES(:id,:email,'Synthetic')"),
            dict(id=user, email=f"{user}@example.invalid"),
        )
        owner.execute(
            text("INSERT INTO memberships(id,tenant_id,user_id) VALUES(:id,:tenant,:user)"),
            dict(id=member, tenant=d["tenant"], user=user),
        )
        owner.execute(
            text("""INSERT INTO resource_query_continuations
          (id,tenant_id,membership_id,user_id,chain_id,source_id,run_id,request_hash,
           snapshot_hash,total_rows,after_offset,expires_at)
          VALUES(:id,:tenant_id,:membership_id,:user_id,:chain_id,:source_id,:run_id,
           :request_hash,:snapshot_hash,:total_rows,100,:expires_at)"""),
            original | dict(id=other, membership_id=member, user_id=user, chain_id=chain),
        )
    with business_transaction(d["engine"], d["identity"]) as session:
        assert (
            session.execute(
                text("SELECT id FROM resource_query_continuations WHERE id=:id"), dict(id=other)
            ).first()
            is None
        )
    with pytest.raises(DBAPIError) as raised:
        with business_transaction(d["engine"], d["identity"]) as session:
            session.execute(
                text("""INSERT INTO resource_query_continuations
              (id,tenant_id,membership_id,user_id,chain_id,source_id,run_id,request_hash,
               snapshot_hash,total_rows,after_offset,next_id,expires_at)
              VALUES(:id,:tenant_id,:membership_id,:user_id,:chain_id,:source_id,:run_id,
               :request_hash,:snapshot_hash,:total_rows,200,:next_id,:expires_at)"""),
                original | dict(id=uuid4(), chain_id=chain, next_id=other),
            )
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert raised.value.orig.sqlstate == "23503"
