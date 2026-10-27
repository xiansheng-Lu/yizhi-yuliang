"""M0-12：只允许有效会话更新本人昵称，不扩大认证角色的users权限。"""

from alembic import op

revision = "0005_profile"
down_revision = "0004_password_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql("""
      CREATE FUNCTION access.update_profile(p_token text,p_name text) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      DECLARE updated uuid;
      BEGIN
        IF p_name IS NULL OR char_length(btrim(p_name)) NOT BETWEEN 1 AND 40 THEN
          RAISE EXCEPTION 'invalid display name' USING ERRCODE='22023'; END IF;
        UPDATE public.users u SET display_name=btrim(p_name)
        WHERE u.status='ACTIVE' AND u.deleted_at IS NULL AND EXISTS (
          SELECT 1 FROM public.sessions s JOIN public.memberships m
            ON (m.tenant_id,m.id,m.user_id)=(s.tenant_id,s.membership_id,s.user_id)
          JOIN public.tenants t ON t.id=s.tenant_id
          WHERE s.token_hash=p_token AND s.user_id=u.id AND s.revoked_at IS NULL
            AND s.expires_at>statement_timestamp() AND s.absolute_expires_at>statement_timestamp()
            AND m.status='ACTIVE' AND t.status='ACTIVE' AND t.deleted_at IS NULL
            AND s.tenant_id=public.current_tenant_id()
        ) RETURNING u.id INTO updated;
        RETURN updated IS NOT NULL;
      END; $$;
      REVOKE ALL ON FUNCTION access.update_profile(text,text) FROM PUBLIC,app_auth;
      GRANT EXECUTE ON FUNCTION access.update_profile(text,text) TO app_login;
    """)


def downgrade() -> None:
    raise RuntimeError("保留身份兼容结构；回退应用或使用前向修复")
