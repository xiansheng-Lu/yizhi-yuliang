-- 不回填旧关联的原生ID；已有历史关联须经实际发现重新核对后才可升级。
ALTER TABLE authorization_account_links ADD COLUMN native_id_snapshot varchar(64);
CREATE FUNCTION access.integration_shop_scope(p_shop uuid,p_permission text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.tenants t ON t.id=m.tenant_id JOIN public.shops s ON s.tenant_id=m.tenant_id
  JOIN public.business_entities e ON (e.tenant_id,e.id)=(s.tenant_id,s.entity_id)
  WHERE m.tenant_id=public.current_tenant_id() AND m.user_id=public.current_actor_id()
   AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND t.status='ACTIVE' AND t.deleted_at IS NULL AND s.deleted_at IS NULL AND s.id=p_shop
   AND e.status='ACTIVE' AND e.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
    WHERE g.membership_id=m.id AND rp.permission_code=p_permission)
   AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.membership_id=m.id
    AND (ds.permissions_mask IS NULL OR p_permission=ANY(ds.permissions_mask))
    AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id)))
$$;
REVOKE ALL ON FUNCTION access.integration_shop_scope(uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.integration_shop_scope(uuid,text) TO app_login;
CREATE TABLE integration_operation_assertions (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,binding_id uuid NOT NULL,account_id uuid NOT NULL,
 business_entity_id uuid NOT NULL,resource varchar(30) NOT NULL,revision bigint NOT NULL CHECK(revision>0),
 payload jsonb NOT NULL,checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,binding_id,resource,revision),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,business_entity_id) REFERENCES business_entities(tenant_id,id),
 CHECK(expires_at>checked_at),
 CHECK((jsonb_typeof(payload)='object' AND payload->>'bindingId'=binding_id::text
  AND payload->>'resource'=resource AND payload->>'id'=id::text) IS TRUE)
);
ALTER TABLE integration_operation_assertions ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration_operation_assertions FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON integration_operation_assertions TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY current_operation_scope ON integration_operation_assertions FOR SELECT TO app_login USING(
 tenant_id=public.current_tenant_id() AND (
  access.configuration_scope(account_id,NULL,business_entity_id,'snapshot.read')
  OR access.configuration_scope(account_id,NULL,business_entity_id,'plan.write')));
GRANT SELECT ON integration_operation_assertions TO app_login;
