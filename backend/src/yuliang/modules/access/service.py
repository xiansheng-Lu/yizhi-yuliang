"""动作权限与对象范围必须同时满足；同租户不构成授权。"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode


def require_shop_account(
    session: Session,
    identity: VerifiedIdentity,
    shop_id: UUID,
    account_id: UUID,
    permission: str,
) -> None:
    allowed = session.execute(
        text("""
        SELECT EXISTS (
          SELECT 1 FROM public.memberships m
          JOIN public.entity_links link ON link.tenant_id=m.tenant_id
          JOIN public.shops shop ON (shop.tenant_id,shop.id)=(link.tenant_id,link.shop_id)
          JOIN public.ad_accounts account ON (account.tenant_id,account.id)
            =(link.tenant_id,link.account_id)
          WHERE m.id=:membership_id AND m.user_id=:user_id AND m.status='ACTIVE'
            AND m.tenant_id=:tenant_id AND m.tenant_id=public.current_tenant_id()
            AND shop.id=:shop_id AND account.id=:account_id
            AND shop.deleted_at IS NULL AND account.deleted_at IS NULL
            AND account.status IN ('BOUND','AUTHORIZED','DEGRADED')
            AND EXISTS (
              SELECT 1 FROM public.role_grants g
              JOIN public.role_permissions p ON (p.tenant_id,p.role_id)=(g.tenant_id,g.role_id)
              WHERE g.tenant_id=m.tenant_id AND g.membership_id=m.id
                AND p.permission_code=:permission
            )
            AND EXISTS (
              SELECT 1 FROM public.data_scopes s
              WHERE s.tenant_id=m.tenant_id AND s.membership_id=m.id
                AND (s.permissions_mask IS NULL OR :permission=ANY(s.permissions_mask))
                AND (s.scope_type='ALL_TENANT' OR s.shop_id=shop.id OR s.entity_id=shop.entity_id)
            )
            AND EXISTS (
              SELECT 1 FROM public.data_scopes s
              WHERE s.tenant_id=m.tenant_id AND s.membership_id=m.id
                AND (s.permissions_mask IS NULL OR :permission=ANY(s.permissions_mask))
                AND (s.scope_type='ALL_TENANT' OR s.account_id=account.id
                  OR s.entity_id=account.entity_id)
            )
        )
        """),
        {
            "tenant_id": identity.tenant_id,
            "membership_id": identity.membership_id,
            "user_id": identity.user_id,
            "shop_id": shop_id,
            "account_id": account_id,
            "permission": permission,
        },
    ).scalar_one()
    if not allowed:
        raise ApiError(ErrorCode.FORBIDDEN, "没有该操作的数据范围")
