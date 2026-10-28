"""本地稿预览纯读；每条正文、引用和断点采用同一事务保存。"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.content.asset_models import document_assets
from yuliang.modules.content.assets import AssetService
from yuliang.modules.content.imports_models import ImportBundle
from yuliang.modules.content.models import Document
from yuliang.modules.content.service import ContentService
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class ContentImports:
    def __init__(self, content: ContentService) -> None:
        if content.shop is None:
            raise ValueError("导入需要明确真实店铺范围")
        self.content = content
        self.shop = content.shop
        self.identity = content.identity
        self.assets = AssetService(content.engine, self.identity, self.shop, content.blob)

    def _owner(self, s: Session, bundle: ImportBundle) -> None:
        self.content._shop(s, self.shop, True)
        if (bundle.owner.tenantId, bundle.owner.userId, bundle.owner.shopId) != (
            str(self.identity.tenant_id),
            str(self.identity.user_id),
            str(self.shop),
        ):
            raise ApiError(ErrorCode.FORBIDDEN, "本地所有者证据与当前真实身份范围不符")

    def _mapped(
        self, s: Session, bundle: ImportBundle, original: Document
    ) -> tuple[Document | None, list[str]]:
        issues: list[str] = []
        value = original.model_dump()
        value["id"] = str(
            bundle.cloud_id(self.identity.tenant_id, self.identity.user_id, self.shop, original.id)
        )
        value["version"] = 1  # local version/revision绝不充当云端并发版本
        if original.parentId:
            parent = next((d for d in bundle.state.documents if d.id == original.parentId), None)
            if parent is None:
                issues.append("LOCAL_PARENT_NOT_IN_MANIFEST")
            elif parent.productId != original.productId:
                issues.append("PARENT_PRODUCT_MISMATCH")
            ancestors = {original.id}
            cursor = parent
            while cursor is not None:
                if cursor.id in ancestors:
                    issues.append("LOCAL_PARENT_CYCLE")
                    break
                ancestors.add(cursor.id)
                cursor = next((d for d in bundle.state.documents if d.id == cursor.parentId), None)
            value["parentId"] = str(
                bundle.cloud_id(
                    self.identity.tenant_id, self.identity.user_id, self.shop, original.parentId
                )
            )
        if original.productId:
            target = bundle.productMap.get(original.productId)
            try:
                source_id, target_id = UUID(original.productId), UUID(target or "")
            except ValueError:
                issues.append("EXPLICIT_PRODUCT_MAPPING_REQUIRED")
            else:
                row = s.execute(
                    text("""SELECT p.native_id,c.native_id AS target_native
                    FROM products p JOIN commerce_products c ON c.id=:target
                    WHERE p.id=:source AND p.shop_id=:shop AND c.shop_id=p.shop_id
                    AND p.deleted_at IS NULL AND c.deleted_at IS NULL"""),
                    dict(source=source_id, target=target_id, shop=self.shop),
                ).first()
                if row is None or row.native_id != row.target_native:
                    issues.append("PRODUCT_MAPPING_NOT_SAME_NATIVE_SHOP")
                else:
                    self.content._product(s, str(target_id), True)
                    value["productId"] = str(target_id)
        local_assets = {a.id: a for a in bundle.state.assets}
        refs = document_assets(original)
        replacements: dict[str, str] = {}
        for local in refs:
            local_id = str(local)
            metadata = local_assets.get(local_id)
            target = bundle.assetMap.get(local_id)
            if metadata is None or target is None:
                issues.append("CONFIRMED_ASSET_MAPPING_REQUIRED")
                continue
            if metadata.productId and metadata.productId != original.productId:
                issues.append("LOCAL_ASSET_PRODUCT_MISMATCH")
                continue
            try:
                row = self.assets._row(s, UUID(target), write=True)
                if metadata.kind != row["kind"]:
                    issues.append("ASSET_KIND_MISMATCH")
                    continue
                if row["product_id"] is not None and str(row["product_id"]) != value["productId"]:
                    issues.append("CLOUD_ASSET_PRODUCT_MISMATCH")
                    continue
                replacements[local_id] = str(row["id"])
            except (ValueError, ApiError):
                issues.append("ASSET_NOT_CURRENT_CONFIRMED_IN_SCOPE")
        if issues:
            return None, sorted(set(issues))
        for node in value["nodes"]:
            if node["assetId"]:
                node["assetId"] = replacements[node["assetId"]]
        for board in value["boards"]:
            for layer in board["layers"]:
                if layer["assetId"]:
                    layer["assetId"] = replacements[layer["assetId"]]
        for clip in value["clips"]:
            if clip["assetId"]:
                clip["assetId"] = replacements[clip["assetId"]]
        for message in value.get("messages") or []:
            if message.get("assetIds"):
                message["assetIds"] = [replacements[v] for v in message["assetIds"]]
        mapped = Document.model_validate(value)
        original_collision = s.execute(
            text("SELECT 1 FROM content_documents WHERE id=:id AND shop_id=:shop"),
            dict(id=UUID(original.id), shop=self.shop),
        ).first()
        if original_collision and bundle.decisions.get(original.id) != "COPY":
            return None, ["CLOUD_ID_CONFLICT_REQUIRES_EXPLICIT_COPY"]
        return mapped, []

    def preview(self, bundle: ImportBundle) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        with business_transaction(self.content.engine, self.identity) as s:
            self._owner(s, bundle)
            args = dict(actor=self.identity.user_id, shop=self.shop, key=bundle.importKey)
            manifest = s.execute(
                text("""SELECT manifest_digest FROM content_imports WHERE
                actor_id=:actor AND shop_id=:shop AND import_key=:key"""),
                args,
            ).scalar_one_or_none()
            if manifest is not None and manifest != bundle.digest():
                raise ApiError(ErrorCode.VERSION_CONFLICT, "导入键内容变化，原断点保持不变")
            for original in bundle.state.documents:
                mapped, issues = self._mapped(s, bundle, original)
                cloud = bundle.cloud_id(
                    self.identity.tenant_id, self.identity.user_id, self.shop, original.id
                )
                existing = s.execute(
                    text("SELECT revision FROM content_documents WHERE id=:id"), dict(id=cloud)
                ).scalar_one_or_none()
                receipt = s.execute(
                    text("""SELECT 1 FROM content_import_entries WHERE
                    actor_id=:actor AND shop_id=:shop AND import_key=:key AND local_id=:local"""),
                    args | dict(local=original.id),
                ).first()
                if existing is not None and receipt is None:
                    issues.append("CLOUD_TARGET_ID_CONFLICT")
                items.append(
                    dict(
                        localId=original.id,
                        cloudId=str(cloud),
                        issues=issues,
                        status="BLOCKED" if issues else "ALREADY_IMPORTED" if existing else "READY",
                        document=None if mapped is None else mapped.model_dump(),
                        accountLinksImported=False,
                    )
                )
        # 仅服务器按固定版本HEAD检查；客户端路径、时长、blob版本不构成证明。
        for item in items:
            if item["status"] != "READY" or item["document"] is None:
                continue
            try:
                self.assets.prepare_document(Document.model_validate(item["document"]))
            except ApiError:
                item["status"] = "BLOCKED"
                item["issues"].append("SERVER_FIXED_FILE_VERIFICATION_REQUIRED")
        return dict(
            importKey=bundle.importKey,
            manifestDigest=bundle.digest(),
            sourceRevision=bundle.state.revision,
            items=items,
            writes=0,
            assetFilesImported=False,
            assets=[
                dict(
                    localId=a.id,
                    mappedCloudId=bundle.assetMap.get(a.id),
                    status="EXISTING_CLOUD_MAPPING"
                    if a.id in bundle.assetMap
                    else "LOCAL_FILE_UPLOAD_REQUIRED",
                )
                for a in bundle.state.assets
            ],
        )

    def _import_lock(self, s: Session, bundle: ImportBundle) -> None:
        s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,760165))"),
            dict(
                lock=f"{self.identity.tenant_id}|{self.identity.user_id}|{self.shop}|{bundle.importKey}"
            ),
        )

    def _receipt_scope(self, s: Session, receipt: dict[str, Any]) -> dict[str, Any]:
        # Replay a historical checkpoint, but never bypass current shop/product permissions.
        row = (
            s.execute(
                text("SELECT shop_id,product_id FROM content_documents WHERE id=:id"),
                dict(id=UUID(receipt["cloudId"])),
            )
            .mappings()
            .one()
        )
        self.content._shop(s, row["shop_id"], True)
        self.content._product(s, str(row["product_id"] or ""), True)
        return dict(receipt)

    def _completed(self, s: Session, bundle: ImportBundle, local_id: str) -> dict[str, Any] | None:
        args = dict(
            actor=self.identity.user_id, shop=self.shop, key=bundle.importKey, local=local_id
        )
        digest = s.execute(
            text("""SELECT manifest_digest FROM content_imports WHERE
            actor_id=:actor AND shop_id=:shop AND import_key=:key"""),
            args,
        ).scalar_one_or_none()
        if digest is not None and digest != bundle.digest():
            raise ApiError(ErrorCode.VERSION_CONFLICT, "同导入键清单已变化，原断点不覆盖")
        receipt = s.execute(
            text("""SELECT receipt FROM content_import_entries WHERE
            actor_id=:actor AND shop_id=:shop AND import_key=:key AND local_id=:local"""),
            args,
        ).scalar_one_or_none()
        return self._receipt_scope(s, receipt) if receipt is not None else None

    def apply(self, bundle: ImportBundle, local_id: str) -> dict[str, Any]:
        original = next((d for d in bundle.state.documents if d.id == local_id), None)
        if original is None:
            raise ApiError(ErrorCode.INVALID_INPUT, "导入条目不在固定清单")
        with business_transaction(self.content.engine, self.identity) as s:
            self._owner(s, bundle)
            self._import_lock(s, bundle)
            completed = self._completed(s, bundle, local_id)
            if completed is not None:
                return completed
            mapped, issues = self._mapped(s, bundle, original)
        if mapped is None:
            raise ApiError(
                ErrorCode.INVALID_INPUT,
                "导入条目未通过预检",
                details=[dict(field="document", code=i) for i in issues],
            )
        proofs = self.assets.prepare_document(mapped)
        cloud = UUID(mapped.id)
        digest = bundle.digest()
        args = dict(
            tenant=self.identity.tenant_id,
            actor=self.identity.user_id,
            shop=self.shop,
            key=bundle.importKey,
            digest=digest,
            revision=bundle.state.revision,
            local=local_id,
            cloud=cloud,
        )
        with business_transaction(self.content.engine, self.identity) as s:
            self._owner(s, bundle)
            s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,760165))"),
                dict(
                    lock=f"{self.identity.tenant_id}|{self.identity.user_id}|{self.shop}|{bundle.importKey}"
                ),
            )
            run = (
                s.execute(
                    text("""SELECT * FROM content_imports WHERE
                actor_id=:actor AND shop_id=:shop AND import_key=:key"""),
                    args,
                )
                .mappings()
                .first()
            )
            if run and run["manifest_digest"] != digest:
                raise ApiError(ErrorCode.VERSION_CONFLICT, "同导入键清单已变化，原断点不覆盖")
            receipt = s.execute(
                text("""SELECT receipt FROM content_import_entries
                WHERE actor_id=:actor AND shop_id=:shop AND import_key=:key AND local_id=:local"""),
                args,
            ).scalar_one_or_none()
            if receipt is not None:
                return self._receipt_scope(s, receipt)
            if run is None:
                s.execute(
                    text("""INSERT INTO content_imports(tenant_id,actor_id,shop_id,
                    import_key,manifest_digest,source_revision)
                    VALUES(:tenant,:actor,:shop,:key,:digest,:revision)"""),
                    args,
                )
            fresh, issues = self._mapped(s, bundle, original)
            if fresh is None or fresh.model_dump() != mapped.model_dump():
                raise ApiError(ErrorCode.LOCKED, "映射或引用已变化，请重新预检")
            if mapped.parentId:
                parent = s.execute(
                    text("""SELECT 1 FROM content_import_entries WHERE
                    actor_id=:actor AND shop_id=:shop AND import_key=:key AND cloud_id=:parent"""),
                    args | dict(parent=UUID(mapped.parentId)),
                ).first()
                if parent is None:
                    raise ApiError(ErrorCode.INVALID_INPUT, "须先导入父文档，断点保持不变")
            # Share the ordinary editor's lock before checking CREATE-ONLY semantics.
            self.content._lock(s, cloud)
            if s.execute(text("SELECT 1 FROM content_documents WHERE id=:cloud"), args).first():
                raise ApiError(
                    ErrorCode.VERSION_CONFLICT, "目标云ID已存在且没有本次导入回执，禁止覆盖"
                )
            saved = self.content._save(s, cloud, mapped, proofs)
            receipt = dict(
                localId=local_id,
                cloudId=str(cloud),
                version=saved["version"],
                manifestDigest=digest,
                assetVersions={str(k): v.version for k, v in proofs.items()},
                productMapping={original.productId: mapped.productId} if original.productId else {},
                assetMappings={str(k): bundle.assetMap[str(k)] for k in document_assets(original)},
            )
            s.execute(
                text("""INSERT INTO content_import_entries(tenant_id,actor_id,shop_id,
                import_key,local_id,cloud_id,receipt)
                VALUES(:tenant,:actor,:shop,:key,:local,:cloud,CAST(:receipt AS jsonb))"""),
                args | dict(receipt=json.dumps(receipt)),
            )
            return receipt

    def progress(self, bundle: ImportBundle) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.identity) as s:
            self._owner(s, bundle)
            args = dict(actor=self.identity.user_id, shop=self.shop, key=bundle.importKey)
            digest = s.execute(
                text("""SELECT manifest_digest FROM content_imports WHERE
                actor_id=:actor AND shop_id=:shop AND import_key=:key"""),
                args,
            ).scalar_one_or_none()
            if digest is not None and digest != bundle.digest():
                raise ApiError(ErrorCode.VERSION_CONFLICT, "导入键内容变化")
            rows = s.execute(
                text("""SELECT local_id,receipt FROM content_import_entries WHERE
                actor_id=:actor AND shop_id=:shop AND import_key=:key ORDER BY local_id"""),
                args,
            ).all()
            return dict(
                importKey=bundle.importKey,
                manifestDigest=bundle.digest(),
                completed=[dict(r.receipt) for r in rows],
                pending=[
                    d.id for d in bundle.state.documents if d.id not in {r.local_id for r in rows}
                ],
            )
