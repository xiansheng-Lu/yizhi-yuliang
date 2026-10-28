"""云稿独立版本、商品范围和在途引用；不使用本地容器revision。"""

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.infrastructure.blob import BlobStore
from yuliang.modules.access.service import require_shop_account
from yuliang.modules.content.asset_models import AssetProof, document_assets
from yuliang.modules.content.models import Document, Kind
from yuliang.modules.content.validation import validate_content
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class DocumentConflict(ApiError):
    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__(ErrorCode.VERSION_CONFLICT, "文档已更新，请保留本地副本并核对当前版本")
        self.current = current


class ContentService:
    def __init__(
        self,
        engine: Engine,
        identity: VerifiedIdentity,
        shop_id: UUID | None = None,
        *,
        blob: BlobStore | None = None,
    ) -> None:
        self.engine, self.identity, self.shop = engine, identity, shop_id
        self.blob = blob

    def _shop(self, s: Session, shop: UUID, write: bool) -> None:
        permission = "content.write" if write else "overview.read"
        allowed = s.execute(
            text("""SELECT EXISTS(SELECT 1 FROM memberships m JOIN shops shop ON
          shop.tenant_id=m.tenant_id
          WHERE m.id=:member AND m.user_id=:user AND m.status='ACTIVE' AND shop.id=:shop AND
          shop.deleted_at IS NULL
          AND EXISTS(SELECT 1 FROM role_grants g JOIN role_permissions p ON
          (p.tenant_id,p.role_id)=(g.tenant_id,g.role_id)
                     WHERE g.membership_id=m.id AND p.permission_code=:permission)
          AND EXISTS(SELECT 1 FROM data_scopes ds WHERE ds.membership_id=m.id
              AND (ds.permissions_mask IS NULL OR :permission=ANY(ds.permissions_mask))
              AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=shop.id OR
          ds.entity_id=shop.entity_id)))"""),
            dict(
                member=self.identity.membership_id,
                user=self.identity.user_id,
                shop=shop,
                permission=permission,
            ),
        ).scalar_one()
        if not allowed or self.shop is not None and self.shop != shop:
            raise ApiError(ErrorCode.FORBIDDEN, "无此店铺内容范围")

    def _product(self, s: Session, product: str, write: bool) -> UUID | None:
        if not product:
            return None
        try:
            product_uuid = UUID(product)
        except ValueError:
            raise ApiError(ErrorCode.INVALID_INPUT, "商品ID格式非法") from None
        row = (
            s.execute(
                text("SELECT * FROM commerce_products WHERE id=:id AND deleted_at IS NULL"),
                dict(id=product_uuid),
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.FORBIDDEN, "商品不在当前租户范围")
        self._shop(s, row["shop_id"], write)
        require_shop_account(
            s,
            self.identity,
            row["shop_id"],
            row["account_id"],
            "content.write" if write else "overview.read",
        )
        return UUID(str(row["shop_id"]))

    def _lock(self, s: Session, identifier: UUID) -> None:
        s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760151))"),
            dict(key=f"{self.identity.tenant_id}|{identifier}"),
        )

    def _row(self, s: Session, identifier: UUID, write: bool = False) -> Any:
        row = (
            s.execute(
                text("SELECT * FROM content_documents WHERE id=:id AND deleted_at IS NULL"),
                dict(id=identifier),
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "文档不存在")
        self._shop(s, row["shop_id"], write)
        self._product(s, str(row["product_id"]) if row["product_id"] else "", write)
        return row

    def get(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as s:
            return dict(self._row(s, identifier)["payload"])

    def save(self, identifier: UUID, document: Document) -> dict[str, Any]:
        if str(identifier) != document.id:
            raise ApiError(ErrorCode.INVALID_INPUT, "路径与文档ID不一致")
        document = validate_content(document)
        proofs = None
        if document_assets(document):
            if self.shop is None:
                raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "素材文档需明确店铺和存储守卫")
            from yuliang.modules.content.assets import AssetService

            proofs = AssetService(
                self.engine, self.identity, self.shop, self.blob
            ).prepare_document(document)
        with business_transaction(self.engine, self.identity) as s:
            return self._save(s, identifier, document, proofs)

    def _save(
        self,
        s: Session,
        identifier: UUID,
        document: Document,
        proofs: dict[UUID, AssetProof] | None = None,
    ) -> dict[str, Any]:
        document = validate_content(document)
        if str(identifier) != document.id:
            from yuliang.modules.content.validation_errors import detail

            raise ApiError(
                ErrorCode.INVALID_INPUT,
                "内容校验未通过",
                details=[detail("id", "ID_MISMATCH", document.model_dump())],
            )
        self._lock(s, identifier)
        old = (
            s.execute(text("SELECT * FROM content_documents WHERE id=:id"), dict(id=identifier))
            .mappings()
            .first()
        )
        product_shop = self._product(s, document.productId, True)
        parent = None
        if document.parentId:
            if document.parentId == document.id:
                raise ApiError(ErrorCode.INVALID_INPUT, "文档不能引用自身")
            parent = self._row(s, UUID(document.parentId), True)
        shop = (
            old["shop_id"] if old else product_shop or (parent["shop_id"] if parent else self.shop)
        )
        if shop is None:
            raise ApiError(ErrorCode.INVALID_INPUT, "无商品的新稿需明确X-Shop-Id店铺范围")
        self._shop(s, shop, True)
        if product_shop is not None and product_shop != shop:
            raise ApiError(ErrorCode.FORBIDDEN, "不可跨店移动文档")
        if parent and (
            parent["shop_id"] != shop or str(parent["product_id"] or "") != document.productId
        ):
            raise ApiError(ErrorCode.FORBIDDEN, "父文档商品或店铺范围不一致")
        if old:
            self._row(s, identifier, True)
            if old["revision"] != document.version:
                raise DocumentConflict(dict(old["payload"]))
            if old["kind"] != document.kind or str(old["parent_id"] or "") != str(
                document.parentId or ""
            ):
                raise ApiError(ErrorCode.INVALID_INPUT, "kind与父引用不可通过保存改写")
            if str(old["product_id"] or "") != document.productId:
                linked = s.execute(
                    text(
                        "SELECT 1 FROM content_document_references WHERE document_id=:id "
                        "AND active "
                        "UNION ALL SELECT 1 FROM content_documents WHERE parent_id=:id "
                        "AND deleted_at IS NULL LIMIT 1"
                    ),
                    dict(id=identifier),
                ).first()
                if linked:
                    raise ApiError(ErrorCode.LOCKED, "有在途引用或派生稿的文档不可改商品")
            revision = old["revision"] + 1
        else:
            if document.version != 1:
                raise ApiError(ErrorCode.INVALID_INPUT, "新文档版本须为1")
            revision = 1
        from yuliang.modules.content.assets import AssetService

        asset_service = AssetService(self.engine, self.identity, shop, self.blob)
        asset_service.validate_document(s, document, proofs)
        value = document.model_dump(exclude_none=True)
        value["parentId"] = document.parentId
        value["version"] = revision
        value["updatedAt"] = datetime.now(UTC).isoformat()
        parameters = dict(
            id=identifier,
            tenant=self.identity.tenant_id,
            shop=shop,
            product=UUID(document.productId) if document.productId else None,
            parent=UUID(document.parentId) if document.parentId else None,
            kind=document.kind,
            revision=revision,
            payload=json.dumps(value),
        )
        if old:
            s.execute(
                text(
                    "UPDATE content_documents SET "
                    "product_id=:product,revision=:revision,payload=CAST(:payload AS "
                    "jsonb),updated_at=clock_timestamp() WHERE id=:id"
                ),
                parameters,
            )
        else:
            s.execute(
                text(
                    "INSERT INTO content_documents(id,tenant_id,shop_id,product_id,parent_id,kind,r"
                    "evision,payload) "
                    "VALUES(:id,:tenant,:shop,:product,:parent,:kind,:revision,CAST(:payload AS "
                    "jsonb))"
                ),
                parameters,
            )
        s.execute(
            text(
                "INSERT INTO content_document_versions(tenant_id,document_id,revision,payload) "
                "VALUES(:tenant,:id,:revision,CAST(:payload AS jsonb))"
            ),
            parameters,
        )
        asset_service.register_document(s, identifier, revision, proofs)
        return value

    def delete(self, identifier: UUID, version: int) -> None:
        with business_transaction(self.engine, self.identity) as s:
            self._lock(s, identifier)
            row = self._row(s, identifier, True)
            if row["revision"] != version:
                raise DocumentConflict(dict(row["payload"]))
            if s.execute(
                text(
                    "SELECT 1 FROM content_document_references WHERE document_id=:id "
                    "AND active "
                    "LIMIT 1"
                ),
                dict(id=identifier),
            ).first():
                raise ApiError(ErrorCode.LOCKED, "文档被在途运行或生成引用")
            s.execute(
                text(
                    "UPDATE content_documents SET deleted_at=clock_timestamp(),revision=revision+1 "
                    "WHERE id=:id"
                ),
                dict(id=identifier),
            )

    def list(
        self, kind: Kind | None = None, product_id: str | None = None, cursor: UUID | None = None
    ) -> list[dict[str, Any]]:
        with business_transaction(self.engine, self.identity) as s:
            if product_id:
                self._product(s, product_id, False)
            rows = (
                s.execute(
                    text(
                        "SELECT * FROM content_documents WHERE deleted_at IS NULL "
                        "AND (CAST(:shop AS uuid) IS NULL OR shop_id=CAST(:shop AS uuid)) "
                        "AND (CAST(:product AS text) IS NULL "
                        "OR COALESCE(product_id::text,'')=:product) "
                        "AND (CAST(:kind AS text) IS NULL OR kind=:kind "
                        "OR (:kind='planning' AND kind='scripts')) "
                        "AND (CAST(:cursor AS uuid) IS NULL "
                        "OR id>CAST(:cursor AS uuid)) ORDER BY id"
                    ),
                    dict(
                        cursor=str(cursor) if cursor else None,
                        shop=str(self.shop) if self.shop else None,
                        product=product_id,
                        kind=kind,
                    ),
                )
                .mappings()
                .all()
            )
            values = []
            for row in rows:
                if kind and row["kind"] not in (
                    {"planning", "scripts"} if kind == "planning" else {kind}
                ):
                    continue
                if product_id is not None and str(row["product_id"] or "") != product_id:
                    continue
                try:
                    self._shop(s, row["shop_id"], False)
                    self._product(s, str(row["product_id"]) if row["product_id"] else "", False)
                except ApiError:
                    continue
                values.append(dict(row["payload"]))
                if len(values) == 100:
                    break
            return values[:100]

    def review(self, identifier: UUID, version: int) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as s:
            row = self._row(s, identifier, True)
            current = validate_content(row["payload"])
        return self.save(
            identifier, current.model_copy(update=dict(version=version, status="review"))
        )

    def flow(self, identifier: UUID, kind: Kind) -> dict[str, Any]:
        """兼容既有内部调用；公共派生必须提供用户稳定请求键和父版本。"""
        from yuliang.modules.content.flows import FlowCreate, FlowService

        with business_transaction(self.engine, self.identity) as session:
            current = self._row(session, identifier, True)
            version = current["revision"]
        return FlowService(self).create(
            identifier,
            FlowCreate(kind=kind, version=version, idempotency_key="internal:" + uuid4().hex),
        )

    def reference(
        self,
        identifier: UUID,
        reference_id: UUID,
        kind: Literal["RUN", "GENERATION"],
        active: bool = True,
    ) -> None:
        """供后续真实运行/生成参与者同事务扩展；当前无公共自授引用HTTP入口。"""
        with business_transaction(self.engine, self.identity) as session:
            self.reference_in_transaction(session, identifier, reference_id, kind, active)

    def reference_in_transaction(
        self,
        session: Session,
        identifier: UUID,
        reference_id: UUID,
        kind: Literal["RUN", "GENERATION"],
        active: bool = True,
    ) -> None:
        """实际run/generation生产者应在创建/终结的同一事务调用此入口。"""
        self._lock(session, identifier)
        self._row(session, identifier, True)
        session.execute(
            text(
                "INSERT INTO content_document_references(tenant_id,document_id,kind,"
                "reference_id,active) "
                "VALUES(:tenant,:doc,:kind,:ref,:active) "
                "ON CONFLICT(tenant_id,document_id,kind,reference_id) "
                "DO UPDATE SET active=EXCLUDED.active"
            ),
            dict(
                tenant=self.identity.tenant_id,
                doc=identifier,
                kind=kind,
                ref=reference_id,
                active=active,
            ),
        )
