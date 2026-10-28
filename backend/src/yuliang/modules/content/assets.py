"""团队素材范围、固定版本读取和归档/引用互斥；不得回退最新对象。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.infrastructure.blob import BlobError, BlobStore, VersionCheck
from yuliang.modules.content.asset_models import AssetProof, document_assets
from yuliang.modules.content.models import Document
from yuliang.modules.content.service import ContentService
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class AssetService:
    def __init__(
        self,
        engine: Engine,
        identity: VerifiedIdentity,
        shop: UUID,
        blob: BlobStore | None,
        *,
        ttl: int = 60,
    ) -> None:
        if not 1 <= ttl <= 300:
            raise ValueError("下载短签有效期非法")
        self.engine, self.identity, self.shop, self.blob, self.ttl = (
            engine,
            identity,
            shop,
            blob,
            ttl,
        )
        self.content = ContentService(engine, identity, shop)

    def _lock(self, s: Session, identifier: UUID) -> None:
        s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760159))"),
            dict(key=f"{self.identity.tenant_id}|{identifier}"),
        )

    def _row(
        self, s: Session, identifier: UUID, *, write: bool = False, archived: bool = False
    ) -> Any:
        row = (
            s.execute(
                text("""SELECT a.* FROM content_assets a JOIN content_uploads u
          ON (u.tenant_id,u.id)=(a.tenant_id,a.id) WHERE a.id=:id AND u.state='CONFIRMED'
          AND u.object_key=a.object_key AND u.bucket=a.bucket AND u.sha256=a.sha256
          AND u.mime=a.mime AND u.size_bytes=a.size_bytes"""),
                dict(id=identifier),
            )
            .mappings()
            .first()
        )
        if row is None or row["deleted_at"] is not None and not archived:
            raise ApiError(ErrorCode.NO_DATA, "素材不存在或已归档")
        self.content._shop(s, row["shop_id"], write)
        self.content._product(s, str(row["product_id"] or ""), write)
        if row["kind"] not in {"image", "text"} or row["duration_state"] != "NOT_APPLICABLE":
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "素材类型或时长尚未验证，不能引用或下载"
            )
        if self.blob is not None and (
            row["bucket"] != self.blob.bucket
            or row["object_key"] != self.blob.key(self.identity.tenant_id, self.shop, identifier)
        ):
            raise ApiError(ErrorCode.FORBIDDEN, "素材对象范围不匹配")
        return row

    def _proof(self, row: Any) -> AssetProof:
        return AssetProof(
            identifier=row["id"],
            tenant=self.identity.tenant_id,
            shop=row["shop_id"],
            product=row["product_id"],
            kind=row["kind"],
            key=row["object_key"],
            bucket=row["bucket"],
            version=row["object_version"],
            size=row["size_bytes"],
            mime=row["mime"],
            sha256=row["sha256"],
            checked_at=datetime.now(UTC),
        )

    @staticmethod
    def metadata(row: Any) -> dict[str, Any]:
        return dict(
            id=str(row["id"]),
            name=row["name"],
            kind=row["kind"],
            productId=str(row["product_id"] or ""),
            documentId=str(row["document_id"]) if row["document_id"] else None,
            mime=row["mime"],
            size=row["size_bytes"],
            duration=float(row["duration"]) if row["duration"] is not None else None,
            durationState=row["duration_state"],
            createdAt=row["created_at"].isoformat(),
            status="CONFIRMED",
        )

    def _check_blob(self, proof: AssetProof) -> AssetProof:
        if self.blob is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "私有素材存储未启用，不能跳过文件检查")
        try:
            check = self.blob.check_version(
                proof.key, proof.version, proof.size, proof.mime, proof.sha256, proof.identifier
            )
            return replace(proof, checked_at=check.checked_at, storage_token=check.token)
        except BlobError:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR, "已确认素材的具体文件版本不可读或缺失，请保留记录核对"
            ) from None

    def _attested(self, proof: AssetProof) -> bool:
        return self.blob is not None and self.blob.valid_version_check(
            VersionCheck(proof.checked_at, proof.storage_token),
            proof.key,
            proof.version,
            proof.size,
            proof.mime,
            proof.sha256,
            proof.identifier,
        )

    def get(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as s:
            return self.metadata(self._row(s, identifier))

    def list(
        self,
        product: str | None = None,
        kind: Literal["image", "text"] | None = None,
        cursor: UUID | None = None,
    ) -> list[dict[str, Any]]:
        with business_transaction(self.engine, self.identity) as s:
            self.content._shop(s, self.shop, False)
            if product:
                self.content._product(s, product, False)
            rows = s.execute(
                text("""SELECT a.id FROM content_assets a JOIN content_uploads u
              ON (a.tenant_id,a.id)=(u.tenant_id,u.id)
              WHERE a.shop_id=:shop AND a.deleted_at IS NULL
              AND u.state='CONFIRMED' AND a.kind IN ('image','text')
              AND (CAST(:product AS text) IS NULL OR COALESCE(a.product_id::text,'')=:product)
              AND (CAST(:kind AS text) IS NULL OR a.kind=:kind)
              AND (CAST(:cursor AS uuid) IS NULL OR a.id>CAST(:cursor AS uuid)) ORDER BY a.id"""),
                dict(
                    shop=self.shop,
                    product=product,
                    kind=kind,
                    cursor=str(cursor) if cursor else None,
                ),
            ).scalars()
            result = []
            for identifier in rows:
                try:
                    row = self._row(s, identifier)
                except ApiError as exc:
                    if exc.code in {
                        ErrorCode.FORBIDDEN,
                        ErrorCode.NO_DATA,
                        ErrorCode.CAPABILITY_UNAVAILABLE,
                    }:
                        continue
                    raise
                result.append(self.metadata(row))
                if len(result) == 100:
                    break
            return result

    def download(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as s:
            proof = self._proof(self._row(s, identifier))
        proof = self._check_blob(proof)
        with business_transaction(self.engine, self.identity) as s:
            self._lock(s, identifier)
            row = self._row(s, identifier)
            if (
                replace(
                    self._proof(row), checked_at=proof.checked_at, storage_token=proof.storage_token
                )
                != proof
            ):
                raise ApiError(ErrorCode.LOCKED, "素材版本已变化，请重新读取")
            if self.blob is None or not self._attested(proof):
                raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "私有素材未启用")
            try:
                url = self.blob.download_url(
                    proof.key,
                    proof.version,
                    expires=self.ttl,
                    check=VersionCheck(
                        proof.checked_at,
                        proof.storage_token,
                        (
                            proof.key,
                            proof.version,
                            proof.size,
                            proof.mime,
                            proof.sha256,
                            proof.identifier,
                        ),
                    ),
                )
            except BlobError:
                raise ApiError(ErrorCode.UPSTREAM_ERROR, "素材下载签名暂不可用") from None
            return dict(
                assetId=str(identifier),
                url=url,
                expiresAt=(datetime.now(UTC) + timedelta(seconds=self.ttl)).isoformat(),
            )

    def prepare_document(self, document: Document) -> dict[UUID, AssetProof]:
        required = document_assets(document)
        proofs = {}
        with business_transaction(self.engine, self.identity) as s:
            for identifier, kinds in required.items():
                row = self._row(s, identifier, write=True)
                self._document_scope(row, document, kinds)
                proofs[identifier] = self._proof(row)
        if not proofs:
            return proofs
        pool = ThreadPoolExecutor(max_workers=4)
        try:
            checked = list(pool.map(self._check_blob, proofs.values(), timeout=30))
            proofs = {v.identifier: v for v in checked}
        except TimeoutError:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, "素材文件核对超时，文档尚未保存") from None
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        return proofs

    @staticmethod
    def _document_scope(row: Any, document: Document, kinds: set[str]) -> None:
        if row["product_id"] is not None and str(row["product_id"]) != document.productId:
            raise ApiError(ErrorCode.FORBIDDEN, "素材和文档商品范围不一致")
        if any(kind != "ANY" and kind != row["kind"] for kind in kinds):
            raise ApiError(ErrorCode.INVALID_INPUT, "图层/剪辑引用的素材类型不匹配")

    def validate_document(
        self, s: Session, document: Document, proofs: dict[UUID, AssetProof] | None
    ) -> None:
        required = document_assets(document)
        if not required:
            if proofs:
                raise ApiError(ErrorCode.INVALID_INPUT, "校验快照与文档素材不匹配")
            return
        if self.blob is None or proofs is None or set(proofs) != set(required):
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "未提供完整私有文件校验守卫，禁止直接保存素材引用"
            )
        for identifier in sorted(required, key=str):
            self._lock(s, identifier)
            row = self._row(s, identifier, write=True)
            self._document_scope(row, document, required[identifier])
            proof = proofs[identifier]
            actual = self._proof(row)
            if replace(
                actual, checked_at=proof.checked_at, storage_token=proof.storage_token
            ) != proof or not self._attested(proof):
                raise ApiError(ErrorCode.LOCKED, "素材状态或验证快照已过期，请重新保存")

    def register_document(
        self, s: Session, document: UUID, revision: int, proofs: dict[UUID, AssetProof] | None
    ) -> None:
        for identifier in proofs or {}:
            s.execute(
                text(
                    "INSERT INTO content_document_asset_versions(tenant_id,document_id"
                    ",revision,asset_id) VALUES(:tenant,:doc,:revision,:asset)"
                ),
                dict(
                    tenant=self.identity.tenant_id,
                    doc=document,
                    revision=revision,
                    asset=identifier,
                ),
            )

    def archive(self, identifier: UUID) -> None:
        with business_transaction(self.engine, self.identity) as s:
            s.execute(
                text("SELECT id FROM content_assets WHERE id=:id FOR NO KEY UPDATE"),
                dict(id=identifier),
            )
            self._lock(s, identifier)
            row = self._row(s, identifier, write=True, archived=True)
            if row["deleted_at"] is not None:
                return
            used = s.execute(
                text(
                    "SELECT 1 FROM content_document_asset_versions WHERE asset_id=:id "
                    "UNION ALL SELECT 1 FROM content_asset_references WHERE "
                    "asset_id=:id AND active LIMIT 1"
                ),
                dict(id=identifier),
            ).first()
            if used:
                raise ApiError(ErrorCode.LOCKED, "素材仍被文档历史或在途运行引用，不能归档")
            s.execute(
                text("UPDATE content_assets SET deleted_at=clock_timestamp() WHERE id=:id"),
                dict(id=identifier),
            )

    def prepare_reference(self, identifier: UUID) -> AssetProof:
        with business_transaction(self.engine, self.identity) as session:
            proof = self._proof(self._row(session, identifier, write=True))
        return self._check_blob(proof)

    def reference_in_transaction(
        self,
        s: Session,
        identifier: UUID,
        kind: Literal["GENERATION", "SOP", "DELIVERY"],
        reference: UUID,
        *,
        active: bool = True,
        proof: AssetProof | None = None,
    ) -> None:
        self._lock(s, identifier)
        row = self._row(s, identifier, write=True, archived=not active)
        if active:
            if (
                self.blob is None
                or proof is None
                or replace(
                    self._proof(row), checked_at=proof.checked_at, storage_token=proof.storage_token
                )
                != proof
                or not self._attested(proof)
            ):
                raise ApiError(
                    ErrorCode.CAPABILITY_UNAVAILABLE, "缺少当前实际文件版本验证，不能登记在途引用"
                )
        s.execute(
            text(
                "INSERT INTO content_asset_references(tenant_id,asset_id,kind,refe"
                "rence_id,active) VALUES(:tenant,:id,:kind,:ref,:active) "
                "ON CONFLICT(tenant_id,asset_id,kind,reference_id) DO UPDATE SET "
                "active=EXCLUDED.active"
            ),
            dict(
                tenant=self.identity.tenant_id,
                id=identifier,
                kind=kind,
                ref=reference,
                active=active,
            ),
        )
