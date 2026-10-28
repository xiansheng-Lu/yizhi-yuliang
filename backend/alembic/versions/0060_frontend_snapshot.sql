CREATE TABLE frontend_snapshot_states (
 tenant_id bigint NOT NULL,user_id uuid NOT NULL,membership_id uuid NOT NULL,
 scope jsonb NOT NULL CHECK(jsonb_typeof(scope)='object'),
 revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
 content_digest varchar(64) NOT NULL DEFAULT '',updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,membership_id),
 FOREIGN KEY(tenant_id,membership_id,user_id) REFERENCES memberships(tenant_id,id,user_id)
);
ALTER TABLE frontend_snapshot_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE frontend_snapshot_states FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON frontend_snapshot_states TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY self_read ON frontend_snapshot_states FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id());
CREATE POLICY self_insert ON frontend_snapshot_states FOR INSERT TO app_login
 WITH CHECK(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id());
CREATE POLICY self_update ON frontend_snapshot_states FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id())
 WITH CHECK(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id());
GRANT SELECT,INSERT,UPDATE ON frontend_snapshot_states TO app_login;
CREATE FUNCTION access.snapshot_member_version(p_member uuid,p_user uuid) RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT m.authz_version FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 JOIN public.tenants t ON t.id=m.tenant_id WHERE m.id=p_member AND m.user_id=p_user
 AND p_user=public.current_actor_id() AND m.tenant_id=public.current_tenant_id()
 AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
 AND t.status='ACTIVE' AND t.deleted_at IS NULL AND EXISTS(SELECT 1
 FROM public.role_grants g JOIN public.role_permissions p
 ON (p.tenant_id,p.role_id)=(g.tenant_id,g.role_id)
 WHERE g.membership_id=m.id AND p.permission_code='snapshot.read');
$$;
REVOKE ALL ON FUNCTION access.snapshot_member_version(uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.snapshot_member_version(uuid,uuid) TO app_login;
