"""按实际前端flowDocument规则创建独立子稿；持久派生服务随后绑定版本与请求键。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import Field, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.analysis.models import AnalysisModel
from yuliang.modules.content.asset_models import document_assets
from yuliang.modules.content.models import Board, Document, Kind, Node, Settings
from yuliang.modules.content.validation import validate_content
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

if TYPE_CHECKING:
    from yuliang.modules.content.service import ContentService


class FlowCreate(AnalysisModel):
    kind: Kind
    version: int = Field(strict=True, ge=1, le=2147483646)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def derive_document(source: Document, kind: Kind) -> Document:
    """只复制brief/body/scenes和明确的editing规格；所有新对象重新生成ID。"""
    source = validate_content(source)
    value = source.model_copy(
        deep=True,
        update=dict(
            id=str(uuid4()),
            kind=kind,
            parentId=source.id,
            version=1,
            status="draft",
            updatedAt=datetime.now(UTC).isoformat(),
            messages=None,
            nodes=[],
            edges=[],
            boards=[],
            clips=[],
            prompt="",
        ),
    )
    names = {
        "planning": "策划",
        "scripts": "脚本",
        "videos": "视频",
        "graphics": "图文",
        "editing": "剪辑",
    }
    value.title = re.sub(r" · (脚本|视频|图文|剪辑)$", "", source.title) + " · " + names[kind]
    if kind != "editing":
        value.settings = Settings(ratio="9:16", duration=15, resolution="1080P", skill="口播精剪")
    for scene in value.scenes:
        scene.id = str(uuid4())
    if kind == "graphics":
        value.boards = [
            Board(
                id=str(uuid4()),
                name="画板 01",
                width=1080,
                height=1350,
                background="#ffffff",
                layers=[],
            )
        ]
        value.prompt = "\n".join(x for x in (source.brief.sellingPoints, source.body) if x)
    if kind == "videos":
        prompt = "\n\n".join(f"{s.visual}\n{s.narration}" for s in source.scenes) or source.body
        value.nodes = [
            Node(id=str(uuid4()), type="reference", x=35, y=60, text="商品与参考素材", assetId=""),
            Node(id=str(uuid4()), type="generate", x=365, y=60, text=prompt, assetId=""),
            Node(id=str(uuid4()), type="result", x=695, y=60, text="成片输出", assetId=""),
        ]
    UUID(value.id)
    return validate_content(value)


class FlowService:
    def __init__(self, content: ContentService):
        self.content = content

    def _replay(self, session: Session, parent: UUID, request: FlowCreate) -> dict[str, Any] | None:
        row = (
            session.execute(
                text(
                    "SELECT * FROM content_derivations WHERE actor_id=:actor AND request_key=:key"
                ),
                dict(actor=self.content.identity.user_id, key=request.idempotency_key),
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        if (row["parent_id"], row["parent_revision"], row["kind"]) != (
            parent,
            request.version,
            request.kind,
        ):
            raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "派生请求键已绑定其他父版本或目标类型")
        try:
            self.content._row(session, row["child_id"], True)
        except ApiError as exc:
            if exc.code == ErrorCode.NO_DATA:
                raise ApiError(
                    ErrorCode.RESOURCE_TERMINAL, "原派生文档已移除，请使用新请求键创建"
                ) from None
            raise
        saved = session.execute(
            text(
                "SELECT payload FROM content_document_versions "
                "WHERE document_id=:id AND revision=:revision"
            ),
            dict(id=row["child_id"], revision=row["child_revision"]),
        ).scalar_one()
        return dict(saved)

    def create(self, parent: UUID, request: FlowCreate) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.content.identity) as session:
            return self.create_in_transaction(session, parent, request)

    def create_in_transaction(
        self,
        session: Session,
        parent: UUID,
        request: FlowCreate,
        *,
        prepared: Document | None = None,
    ) -> dict[str, Any]:
        """参加调用方事务；不提交子稿，也不打开第二个数据库事务。"""
        from yuliang.modules.content.service import DocumentConflict

        try:
            request = FlowCreate.model_validate(request.model_dump(warnings=False))
        except ValidationError:
            raise ApiError(ErrorCode.INVALID_INPUT, "派生请求参数无效") from None
        c = self.content
        # 派生按前端规则清空所有文件引用；不能凭复制source.assetId跳过实际文件守卫。
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760163))"),
            dict(key=f"{c.identity.tenant_id}|{c.identity.user_id}|{request.idempotency_key}"),
        )
        c._lock(session, parent)
        current = c._row(session, parent, True)
        if prepared is not None:
            prepared = validate_content(prepared)
            if (
                prepared.parentId != str(parent)
                or prepared.productId != current["payload"]["productId"]
                or prepared.kind != request.kind
                or prepared.version != 1
                or prepared.id == str(parent)
                or prepared.status != "draft"
                or document_assets(prepared)
            ):
                raise ApiError(ErrorCode.INVALID_INPUT, "冻结派生稿的身份、版本或素材引用不合法")
        replay = self._replay(session, parent, request)
        if replay is not None:
            if prepared is not None and Document.model_validate(replay).model_dump(
                exclude={"updatedAt"}
            ) != prepared.model_dump(exclude={"updatedAt"}):
                raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "派生请求键的冻结候选已变化")
            return replay
        if current["revision"] != request.version:
            raise DocumentConflict(dict(current["payload"]))
        child = prepared or derive_document(validate_content(current["payload"]), request.kind)
        c._lock(session, UUID(child.id))
        if session.execute(
            text("SELECT 1 FROM content_documents WHERE id=:id"), dict(id=UUID(child.id))
        ).first():
            raise ApiError(ErrorCode.VERSION_CONFLICT, "派生目标ID已存在且没有原请求回执，禁止覆盖")
        saved = c._save(session, UUID(child.id), child)
        session.execute(
            text(
                "INSERT INTO content_derivations(tenant_id,actor_id,request_key,parent_id,"
                "parent_revision,child_id,child_revision,kind) "
                "VALUES(:tenant,:actor,:key,:parent,:revision,:child,1,:kind)"
            ),
            dict(
                tenant=c.identity.tenant_id,
                actor=c.identity.user_id,
                key=request.idempotency_key,
                parent=parent,
                revision=request.version,
                child=child.id,
                kind=request.kind,
            ),
        )
        return saved
