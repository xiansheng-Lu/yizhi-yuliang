"""M0-08a：四段策略及只读系统字典；所有者维护策略不授予应用。"""

from alembic import op

revision = "0002_tenant_policies"
down_revision = "0001_identity_catalog"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "memberships",
    "business_entities",
    "shops",
    "ad_accounts",
    "entity_links",
    "live_sessions",
    "products",
    "roles",
    "role_permissions",
    "role_grants",
    "data_scopes",
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("""
        CREATE FUNCTION public.current_tenant_id() RETURNS bigint
        LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
        DECLARE value bigint;
        BEGIN
          value := nullif(current_setting('app.current_tenant', true), '')::bigint;
          IF value > 0 THEN RETURN value; END IF;
          RETURN NULL;
        EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
          RETURN NULL;
        END; $$;
        REVOKE ALL ON FUNCTION public.current_tenant_id() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.current_tenant_id() TO app_login;
    """)
    for name in (
        *TENANT_TABLES,
        "tenants",
        "permissions",
        "role_templates",
        "role_template_permissions",
    ):
        bind.exec_driver_sql(
            f"CREATE POLICY {name}_maintenance ON public.{name} FOR ALL TO app_owner "
            "USING (true) WITH CHECK (true)"
        )
    for name in TENANT_TABLES:
        predicate = "tenant_id = public.current_tenant_id()"
        bind.exec_driver_sql(
            f"CREATE POLICY {name}_select ON public.{name} FOR SELECT TO "
            f"app_login USING ({predicate});"
            f"CREATE POLICY {name}_insert ON public.{name} FOR INSERT TO "
            f"app_login WITH CHECK ({predicate});"
            f"CREATE POLICY {name}_update ON public.{name} FOR UPDATE TO "
            f"app_login USING ({predicate}) WITH CHECK ({predicate});"
            f"CREATE POLICY {name}_delete ON public.{name} FOR DELETE TO "
            f"app_login USING ({predicate})"
        )
    # 目录写由服务用例控制；成员/角色/范围的运行身份仅可读，防绕过管理用例自授权。
    for name in TENANT_TABLES:
        actions = (
            "SELECT"
            if name in ("memberships", "roles", "role_permissions", "role_grants", "data_scopes")
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        bind.exec_driver_sql(f"GRANT {actions} ON public.{name} TO app_login")
    bind.exec_driver_sql(
        "CREATE POLICY tenants_select ON public.tenants FOR SELECT TO app_login "
        "USING (id = public.current_tenant_id()); GRANT SELECT ON public.tenants TO app_login"
    )
    for name in ("permissions", "role_templates", "role_template_permissions"):
        bind.exec_driver_sql(
            f"CREATE POLICY {name}_read ON public.{name} FOR SELECT TO app_login "
            "USING (public.current_tenant_id() IS NOT NULL);"
            f"GRANT SELECT ON public.{name} TO app_login"
        )


def downgrade() -> None:
    raise RuntimeError("撤销隔离策略会改变安全边界；回退应用并保留结构，修复使用前向迁移")
