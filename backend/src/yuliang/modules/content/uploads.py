"""上传计划先持久化，完成确认事务外核对真实版本，再以token栅栏登记素材。"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.infrastructure.blob import BlobError, BlobStore
from yuliang.modules.content.service import ContentService
from yuliang.modules.content.uploads_models import UploadCreate
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class UploadService:
    def __init__(
        self,
        engine: Engine,
        identity: VerifiedIdentity,
        shop: UUID,
        blob: BlobStore,
        *,
        ttl: int = 300,
    ) -> None:
        if type(ttl) is not int or not 1 <= ttl <= 900:
            raise ValueError("上传有效期非法")
        self.engine, self.identity, self.shop, self.blob, self.ttl = (
            engine,
            identity,
            shop,
            blob,
            ttl,
        )
        self.content = ContentService(engine, identity, shop)

    def _scope(self, s: Session, product: str, document: str | None) -> None:
        self.content._shop(s, self.shop, True)
        self.content._product(s, product, True)
        if document:
            row = self.content._row(s, UUID(document), True)
            if str(row["product_id"] or "") != product:
                raise ApiError(ErrorCode.FORBIDDEN, "文档和素材商品范围不一致")

    def _row(self, s: Session, identifier: UUID) -> Any:
        row = (
            s.execute(
                text("SELECT * FROM content_uploads WHERE id=:id FOR UPDATE"), dict(id=identifier)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "上传计划不存在")
        if row["actor_id"] != self.identity.user_id or row["shop_id"] != self.shop:
            raise ApiError(ErrorCode.FORBIDDEN, "无此上传计划范围")
        self._scope(
            s, str(row["product_id"] or ""), str(row["document_id"]) if row["document_id"] else None
        )
        if row["bucket"] != self.blob.bucket or row["object_key"] != self.blob.key(
            self.identity.tenant_id, self.shop, identifier
        ):
            raise ApiError(ErrorCode.FORBIDDEN, "对象存储范围与计划不一致")
        return row

    def create(self, value: UploadCreate) -> dict[str, Any]:
        digest = hashlib.sha256(
            json.dumps(
                value.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        with business_transaction(self.engine, self.identity) as s:
            self._scope(s, value.productId, value.documentId)
            s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760154))"),
                dict(
                    key=f"{self.identity.tenant_id}|{self.identity.user_id}|{value.clientRequestKey}"
                ),
            )
            old = (
                s.execute(
                    text(
                        "SELECT * FROM content_uploads WHERE actor_id=:actor AND "
                        "client_request_key=:key"
                    ),
                    dict(actor=self.identity.user_id, key=UUID(value.clientRequestKey)),
                )
                .mappings()
                .first()
            )
            if old:
                if old["request_digest"] != digest:
                    raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "原上传键对应另一份文件或范围")
                identifier = old["id"]
            else:
                identifier = uuid4()
                s.execute(
                    text("""INSERT INTO content_uploads(id,tenant_id,shop_id,product_id,document_id,
                actor_id,client_request_key,request_digest,name,kind,mime,size_bytes,sha256,object_key,bucket,state,expires_at)
                VALUES(:id,:tenant,:shop,:product,:document,:actor,:key,:digest,:name,:kind,:mime,:size,:sha,
                :object,:bucket,'PENDING',clock_timestamp()+make_interval(secs=>:ttl))"""),
                    dict(
                        id=identifier,
                        tenant=self.identity.tenant_id,
                        shop=self.shop,
                        product=UUID(value.productId) if value.productId else None,
                        document=UUID(value.documentId) if value.documentId else None,
                        actor=self.identity.user_id,
                        key=UUID(value.clientRequestKey),
                        digest=digest,
                        name=value.name,
                        kind=value.kind,
                        mime=value.mime,
                        size=value.size,
                        sha=value.sha256,
                        object=self.blob.key(self.identity.tenant_id, self.shop, identifier),
                        bucket=self.blob.bucket,
                        ttl=self.ttl,
                    ),
                )
        return self.get(identifier)

    def _asset(self, s: Session, identifier: UUID) -> dict[str, Any]:
        row = (
            s.execute(
                text("SELECT * FROM content_assets WHERE id=:id AND deleted_at IS NULL"),
                dict(id=identifier),
            )
            .mappings()
            .one()
        )
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

    def get(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as s:
            row = self._row(s, identifier)
            result = dict(
                uploadId=str(identifier),
                state=row["state"],
                expiresAt=row["expires_at"].isoformat(),
                asset=self._asset(s, identifier) if row["state"] == "CONFIRMED" else None,
            )
            key, size, mime, sha = row["object_key"], row["size_bytes"], row["mime"], row["sha256"]
            remaining = int((row["expires_at"] - datetime.now(UTC)).total_seconds())
        if result["state"] == "CONFIRMED":
            return result
        if remaining < 1:
            raise ApiError(
                ErrorCode.RESOURCE_TERMINAL, "上传计划已过期，请保留原记录并建立新请求键"
            )
        try:
            result["upload"] = self.blob.upload_url(
                key, size, mime, sha, identifier, expires=min(remaining, self.ttl)
            )
        except BlobError:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, "私有上传签名暂不可用") from None
        return result

    def complete(self, identifier: UUID) -> dict[str, Any]:
        token = uuid4()
        with business_transaction(self.engine, self.identity) as s:
            row = self._row(s, identifier)
            if row["state"] == "CONFIRMED":
                return self._asset(s, identifier)
            now = s.execute(text("SELECT clock_timestamp()")).scalar_one()
            if row["expires_at"] <= now:
                raise ApiError(ErrorCode.RESOURCE_TERMINAL, "上传计划已过期")
            if row["state"] == "VERIFYING" and row["verify_until"] > now:
                raise ApiError(ErrorCode.LOCKED, "该上传正在核验，请查询原计划")
            s.execute(
                text(
                    "UPDATE content_uploads SET state='VERIFYING',verify_token=:token,"
                    "verify_until=clock_timestamp()+interval '90 "
                    "seconds',last_error=NULL WHERE id=:id"
                ),
                dict(token=token, id=identifier),
            )
            key, size, mime, sha = row["object_key"], row["size_bytes"], row["mime"], row["sha256"]
        try:
            verified = self.blob.verify(key, size, mime, sha, identifier)
        except BlobError as exc:
            with business_transaction(self.engine, self.identity) as s:
                s.execute(
                    text(
                        "UPDATE content_uploads SET state='PENDING',verify_token=NULL,veri"
                        "fy_until=NULL,last_error=:reason WHERE id=:id AND "
                        "verify_token=:token"
                    ),
                    dict(id=identifier, token=token, reason=exc.reason),
                )
            code = (
                ErrorCode.INVALID_INPUT
                if exc.reason
                in {
                    "OBJECT_METADATA_MISMATCH",
                    "OBJECT_SIZE_MISMATCH",
                    "OBJECT_CHECKSUM_MISMATCH",
                    "OBJECT_TYPE_MISMATCH",
                }
                else ErrorCode.UPSTREAM_ERROR
            )
            raise ApiError(
                code,
                "文件未通过实际对象校验，尚未登记为可用素材",
                details=[dict(field="file", reason=exc.reason)],
            ) from None
        with business_transaction(self.engine, self.identity) as s:
            row = self._row(s, identifier)
            now = s.execute(text("SELECT clock_timestamp()")).scalar_one()
            if row["state"] == "CONFIRMED":
                return self._asset(s, identifier)
            if row["verify_token"] != token or row["verify_until"] < now:
                raise ApiError(ErrorCode.LOCKED, "核验租约已变化，请查询原计划")
            s.execute(
                text("""INSERT INTO content_assets(id,tenant_id,shop_id,product_id,document_id,
                name,kind,mime,size_bytes,sha256,object_key,bucket,object_version,duration,duration_state)
                VALUES(:id,:tenant,:shop,:product,:document,:name,:kind,:mime,:size,:sha,:object,:bucket,:version,:duration,:durationstate)"""),
                dict(
                    id=identifier,
                    tenant=self.identity.tenant_id,
                    shop=self.shop,
                    product=row["product_id"],
                    document=row["document_id"],
                    name=row["name"],
                    kind=row["kind"],
                    mime=mime,
                    size=size,
                    sha=sha,
                    object=key,
                    bucket=self.blob.bucket,
                    version=verified.version_id,
                    duration=0 if row["kind"] in {"image", "text"} else None,
                    durationstate="NOT_APPLICABLE"
                    if row["kind"] in {"image", "text"}
                    else "UNCONFIRMED",
                ),
            )
            s.execute(
                text(
                    "UPDATE content_uploads SET state='CONFIRMED',confirmed_at=clock_t"
                    "imestamp(),verify_token=NULL,verify_until=NULL WHERE id=:id"
                ),
                dict(id=identifier),
            )
            return self._asset(s, identifier)
