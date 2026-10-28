"""素材账号交接仅记录意图；没有token申请/平台调用/UPLOAD授权。"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.content.account_links_models import LinkChange
from yuliang.modules.content.assets import AssetService
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class AccountLinks:
    def __init__(self, assets: AssetService) -> None:
        self.assets = assets
        self.content = assets.content
        self.identity = assets.identity
        self.shop = assets.shop

    def _asset(self, s: Session, identifier: UUID, write: bool, *, archived: bool = False) -> Any:
        row = (
            s.execute(text("SELECT * FROM content_assets WHERE id=:id"), dict(id=identifier))
            .mappings()
            .first()
        )
        if (
            row is None
            or row["shop_id"] != self.shop
            or row["deleted_at"] is not None
            and not archived
        ):
            raise ApiError(ErrorCode.NO_DATA, "素材不存在或已归档")
        self.content._shop(s, self.shop, write)
        self.content._product(s, str(row["product_id"] or ""), write)
        return row

    def _target(self, s: Session, line: str, account: UUID) -> dict[str, Any]:
        require_shop_account(s, self.identity, self.shop, account, "content.write")
        row = s.execute(
            text("""SELECT * FROM access.content_account_link_target(
            :account,:shop,:line)"""),
            dict(account=account, shop=self.shop, line=line),
        ).first()
        if row is None:
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "账户主体/原生快照/OAuth或当前范围未通过交接意图核验",
            )
        return dict(
            accountId=str(account),
            accountName=row.name,
            nativeId=row.native_id,
            productLine=line,
            businessEntityId=str(row.entity_id),
            tenantId=str(self.identity.tenant_id),
            shopId=str(self.shop),
            mode="API",
        )

    @staticmethod
    def view(row: Any) -> dict[str, Any]:
        link = dict(row["snapshot"])
        return dict(
            id=str(row["id"]),
            version=row["revision"],
            intent="LINKED" if row["active"] else "CANCELLED",
            link=link,
            cancelledAt=row["cancelled_at"].isoformat() if row["cancelled_at"] else None,
            assetVersion=row["asset_version"],
            upload=dict(
                status="NOT_INTEGRATED",
                platformAssetId=None,
                reason="尚无正式平台素材上传证据生产者；私有存储确认不等于平台上传",
            ),
            review=dict(status="UNKNOWN", reason="尚无平台素材审核独立读取证据"),
        )

    def link(self, asset: UUID, value: LinkChange) -> dict[str, Any]:
        value = LinkChange.model_validate(value.model_dump())
        account = UUID(value.accountId)
        proof = self.assets.prepare_reference(asset)
        with business_transaction(self.assets.engine, self.identity) as s:
            self.assets._lock(s, asset)
            self._asset(s, asset, True)
            row = self.assets._row(s, asset, write=True)
            if replace(
                self.assets._proof(row),
                checked_at=proof.checked_at,
                storage_token=proof.storage_token,
            ) != proof or not self.assets._attested(proof):
                raise ApiError(ErrorCode.LOCKED, "素材固定版本证明已变化，请重新确认")
            target = self._target(s, value.productLine, account)
            old = (
                s.execute(
                    text("""SELECT * FROM content_asset_account_links WHERE asset_id=:asset
                AND product_line=:line AND account_id=:account FOR UPDATE"""),
                    dict(asset=asset, line=value.productLine, account=account),
                )
                .mappings()
                .first()
            )
            version = old["revision"] if old else 0
            if old and old["active"] and value.expectedVersion in (0, version):
                # 同键幂等只复用原意图；已换素材版本/原生账户不能隐藏变化。
                if (
                    old["asset_version"] != proof.version
                    or old["asset_sha256"] != proof.sha256
                    or any(
                        old["snapshot"][k] != target[k] for k in ("nativeId", "businessEntityId")
                    )
                ):
                    raise ApiError(ErrorCode.VERSION_CONFLICT, "现有意图的素材或账户快照已变化")
                return self.view(old)
            if value.expectedVersion != version:
                raise ApiError(ErrorCode.VERSION_CONFLICT, "账号关联意图版本已变化")
            identifier = old["id"] if old else uuid4()
            now = datetime.now(UTC)
            snapshot = target | dict(linkedAt=now.isoformat(), linkedBy=str(self.identity.user_id))
            args = dict(
                id=identifier,
                tenant=self.identity.tenant_id,
                shop=self.shop,
                asset=asset,
                line=value.productLine,
                account=account,
                revision=version + 1,
                snapshot=json.dumps(snapshot),
                version=proof.version,
                sha=proof.sha256,
                actor=self.identity.user_id,
                at=now,
            )
            if old is None:
                s.execute(
                    text("""INSERT INTO content_asset_account_links(id,tenant_id,shop_id,asset_id,
                    account_id,product_line,revision,active,snapshot,asset_version,asset_sha256,
                    linked_by,linked_at)
                    VALUES(:id,:tenant,:shop,:asset,:account,:line,:revision,true,
                    CAST(:snapshot AS jsonb),
                    :version,:sha,:actor,:at)"""),
                    args,
                )
            else:
                s.execute(
                    text("""UPDATE content_asset_account_links SET revision=:revision,active=true,
                    snapshot=CAST(:snapshot AS jsonb),asset_version=:version,asset_sha256=:sha,
                    linked_by=:actor,linked_at=:at,cancelled_by=NULL,cancelled_at=NULL
                    WHERE id=:id"""),
                    args,
                )
            self._event(s, args, "LINKED")
            return self.view(
                s.execute(text("SELECT * FROM content_asset_account_links WHERE id=:id"), args)
                .mappings()
                .one()
            )

    def _event(self, s: Session, args: dict[str, Any], action: str) -> None:
        s.execute(
            text("""INSERT INTO content_asset_account_link_events(tenant_id,link_id,revision,
            actor_id,action,snapshot)
            VALUES(:tenant,:id,:revision,:actor,:action,CAST(:snapshot AS jsonb))"""),
            args | dict(action=action),
        )

    def cancel(self, asset: UUID, identifier: UUID, expected: int) -> dict[str, Any]:
        # 取消不申请token、不HEAD，不要求目标OAuth恢复；当前素材店铺/商品管理权仍必须有。
        with business_transaction(self.assets.engine, self.identity) as s:
            self.assets._lock(s, asset)
            self._asset(s, asset, True, archived=True)
            old = (
                s.execute(
                    text("""SELECT * FROM content_asset_account_links
                WHERE id=:id AND asset_id=:asset FOR UPDATE"""),
                    dict(id=identifier, asset=asset),
                )
                .mappings()
                .first()
            )
            if old is None:
                raise ApiError(ErrorCode.NO_DATA, "账号关联意图不存在")
            if not old["active"] and expected in (old["revision"], old["revision"] - 1):
                return self.view(old)
            if expected != old["revision"]:
                raise ApiError(ErrorCode.VERSION_CONFLICT, "账号关联意图版本已变化")
            args = dict(
                id=identifier,
                revision=old["revision"] + 1,
                actor=self.identity.user_id,
                tenant=self.identity.tenant_id,
                snapshot=json.dumps(old["snapshot"]),
            )
            s.execute(
                text("""UPDATE content_asset_account_links SET active=false,revision=:revision,
                cancelled_by=:actor,cancelled_at=clock_timestamp() WHERE id=:id"""),
                args,
            )
            self._event(s, args, "CANCELLED")
            return self.view(
                s.execute(text("SELECT * FROM content_asset_account_links WHERE id=:id"), args)
                .mappings()
                .one()
            )

    def list(self, asset: UUID) -> list[dict[str, Any]]:
        with business_transaction(self.assets.engine, self.identity) as s:
            self._asset(s, asset, False, archived=True)
            rows = (
                s.execute(
                    text("""SELECT * FROM content_asset_account_links WHERE asset_id=:asset
                ORDER BY product_line,account_id LIMIT 1001"""),
                    dict(asset=asset),
                )
                .mappings()
                .all()
            )
            if len(rows) > 1000:
                raise ApiError(ErrorCode.INVALID_INPUT, "账号意图超过1000条；拒绝截断列表")
            visible = []
            for row in rows:
                try:
                    require_shop_account(
                        s, self.identity, self.shop, row["account_id"], "overview.read"
                    )
                except ApiError:
                    continue
                visible.append(self.view(row))
            return visible
