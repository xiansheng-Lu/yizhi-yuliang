"""可核对的API快照：同MVCC事务裁剪数据、持久scope与单调revision。"""

import hashlib
import json
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.snapshot.models import ReportingScope, initial_scope
from yuliang.modules.snapshot.projections import project
from yuliang.modules.snapshot.scope import require_member, validate_scope, visible
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class SnapshotService:
    def __init__(self, engine: Engine, identity: VerifiedIdentity) -> None:
        self.engine, self.identity = engine, identity

    def _transaction(self, scope: ReportingScope | None) -> dict[str, Any] | None:
        engine = self.engine.execution_options(isolation_level="REPEATABLE READ")
        with business_transaction(engine, self.identity) as s:
            member = require_member(s, self.identity)
            params = dict(
                tenant=self.identity.tenant_id,
                member=self.identity.membership_id,
                user=self.identity.user_id,
            )
            s.execute(
                text("""INSERT INTO
frontend_snapshot_states(tenant_id,membership_id,user_id,scope)
VALUES(:tenant,:member,:user,CAST(:scope AS jsonb)) ON CONFLICT
DO NOTHING"""),
                params | dict(scope=initial_scope().model_dump_json()),
            )
            state = (
                s.execute(
                    text(
                        """SELECT * FROM frontend_snapshot_states WHERE
membership_id=:member AND user_id=:user FOR UPDATE"""
                    ),
                    params,
                )
                .mappings()
                .one()
            )
            actual = scope or ReportingScope.model_validate(state["scope"])
            shops, pairs = visible(s, self.identity, actual)
            if scope is not None:
                validate_scope(s, actual, shops, pairs)
                s.execute(
                    text(
                        """UPDATE frontend_snapshot_states SET scope=CAST(:scope AS jsonb)
WHERE membership_id=:member AND user_id=:user"""
                    ),
                    params | dict(scope=actual.model_dump_json()),
                )
                return None
            try:
                validate_scope(s, actual, shops, pairs)
            except ApiError as error:
                if error.code != ErrorCode.FORBIDDEN:
                    raise
                # 撤权后不继续使用失效选择；重置仅为当前授权范围的all，不能扩大权限。
                actual = initial_scope().model_copy(update=dict(date=actual.date))
                shops, pairs = visible(s, self.identity, actual)
                validate_scope(s, actual, shops, pairs)
            data = project(s, self.identity, actual, shops, pairs)
            if data["gaps"]:
                unique = {tuple(sorted(g.items())) for g in data["gaps"]}
                raise ApiError(
                    ErrorCode.DATA_COVERAGE_INSUFFICIENT,
                    "当前范围尚缺完整且口径明确的快照投影",
                    details=[dict(pairs) for pairs in sorted(unique)][:100],
                )
            value: dict[str, Any] = data["arrays"] | dict(
                schemaVersion="command-job-v1.0", source="API", scope=actual.model_dump(mode="json")
            )
            digest = hashlib.sha256(
                json.dumps(
                    value | dict(authzVersion=member["authz_version"]),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            revision = state["revision"] + (digest != state["content_digest"])
            if revision > 9007199254740991:
                raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "快照版本超出客户端精度范围")
            if (
                digest != state["content_digest"]
                or actual.model_dump(mode="json") != state["scope"]
            ):
                s.execute(
                    text("""UPDATE frontend_snapshot_states SET scope=CAST(:scope AS
    jsonb),revision=:revision,
    content_digest=:digest,updated_at=clock_timestamp() WHERE
    membership_id=:member AND user_id=:user"""),
                    params | dict(scope=actual.model_dump_json(), revision=revision, digest=digest),
                )
            return value | dict(revision=revision)

    def get(self) -> dict[str, Any]:
        for attempt in range(3):
            try:
                result = self._transaction(None)
                if result is None:
                    raise ApiError(ErrorCode.INTERNAL_ERROR, "快照未生成")
                return result
            except DBAPIError as error:
                if getattr(error.orig, "sqlstate", None) != "40001" or attempt == 2:
                    raise
        raise ApiError(ErrorCode.VERSION_CONFLICT, "快照并发更新，请重试")

    def set_scope(self, scope: ReportingScope) -> None:
        for attempt in range(3):
            try:
                self._transaction(scope)
                return
            except DBAPIError as error:
                if getattr(error.orig, "sqlstate", None) != "40001" or attempt == 2:
                    raise
