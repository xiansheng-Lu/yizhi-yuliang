"""M0-06：身份/目录最小表及权限骨架；租户表初始deny-all。"""

from pathlib import Path

from alembic import op

revision = "0001_identity_catalog"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)
    op.get_bind().exec_driver_sql(
        (Path(__file__).parent / "0001_access_skeleton.sql").read_text(encoding="utf-8")
    )
    op.get_bind().exec_driver_sql("""
        CREATE FUNCTION public.set_updated_at() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
        BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;
        REVOKE ALL ON FUNCTION public.set_updated_at() FROM PUBLIC;
    """)
    for name in (
        "tenants",
        "users",
        "memberships",
        "business_entities",
        "shops",
        "ad_accounts",
        "live_sessions",
        "products",
        "roles",
    ):
        op.get_bind().exec_driver_sql(
            f"CREATE TRIGGER trg_{name}_updated BEFORE UPDATE ON public.{name} "
            "FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()"
        )
    # 未验RLS前即默认拒绝：业务角色没有表权限，且所有者也受FORCE约束。
    for name in (
        "tenants",
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
        "permissions",
        "role_templates",
        "role_template_permissions",
    ):
        op.get_bind().exec_driver_sql(
            f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY; "
            f"ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY"
        )


def downgrade() -> None:
    raise RuntimeError("基础身份数据禁止自动删表回退；回退应用并保留兼容结构，见迁移说明")
