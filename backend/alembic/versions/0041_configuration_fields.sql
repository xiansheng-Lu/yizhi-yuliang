CREATE FUNCTION access.configuration_scope(p_account uuid,p_shop uuid,p_entity uuid,p_permission text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.tenants t ON t.id=m.tenant_id JOIN public.ad_accounts a ON a.tenant_id=m.tenant_id
  JOIN public.business_entities e ON (e.tenant_id,e.id)=(a.tenant_id,a.entity_id)
  JOIN public.entity_links link ON (link.tenant_id,link.account_id)=(a.tenant_id,a.id)
  JOIN public.shops s ON (s.tenant_id,s.id)=(link.tenant_id,link.shop_id)
  JOIN public.business_entities se ON (se.tenant_id,se.id)=(s.tenant_id,s.entity_id)
  WHERE m.tenant_id=public.current_tenant_id() AND m.user_id=public.current_actor_id()
   AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND t.status='ACTIVE' AND t.deleted_at IS NULL AND a.id=p_account AND a.entity_id=p_entity
   AND a.deleted_at IS NULL AND a.status IN ('BOUND','AUTHORIZED','DEGRADED')
   AND e.deleted_at IS NULL AND e.status='ACTIVE' AND s.deleted_at IS NULL
   AND se.deleted_at IS NULL AND se.status='ACTIVE'
   AND (p_shop IS NULL OR s.id=p_shop)
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
    WHERE g.membership_id=m.id AND rp.permission_code=p_permission)
   AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.membership_id=m.id
    AND (ds.permissions_mask IS NULL OR p_permission=ANY(ds.permissions_mask))
    AND (ds.scope_type='ALL_TENANT' OR ds.account_id=a.id OR ds.entity_id=a.entity_id))
   AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.membership_id=m.id
    AND (ds.permissions_mask IS NULL OR p_permission=ANY(ds.permissions_mask))
    AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id)))
$$;
REVOKE ALL ON FUNCTION access.configuration_scope(uuid,uuid,uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.configuration_scope(uuid,uuid,uuid,text) TO app_login;

CREATE TABLE configuration_field_assertions (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,account_id uuid NOT NULL,business_entity_id uuid NOT NULL,
 shop_id uuid,context_key text NOT NULL CHECK(octet_length(context_key)<=65536),
 context_digest varchar(64) NOT NULL,field_id varchar(40) NOT NULL CHECK(field_id ~ '^[A-Za-z][A-Za-z0-9]{0,39}$'),
 revision bigint NOT NULL CHECK(revision>0),binding_id uuid,authorization_id uuid,
 payload jsonb NOT NULL,
 checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,account_id,context_digest,field_id,revision),
 FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 FOREIGN KEY(tenant_id,business_entity_id) REFERENCES business_entities(tenant_id,id),
 FOREIGN KEY(tenant_id,shop_id) REFERENCES shops(tenant_id,id),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,authorization_id) REFERENCES provider_authorizations(tenant_id,id),
 CHECK(context_digest=encode(sha256(convert_to(context_key,'UTF8')),'hex')),
 CHECK(expires_at>checked_at),CHECK(jsonb_typeof(payload)='object'),
 CHECK(payload#>>'{context,accountId}'=account_id::text
   AND payload#>>'{context,businessEntityId}'=business_entity_id::text
   AND payload->>'fieldId'=field_id),
 CHECK(payload#>>'{capability,state}' IN
   ('PUBLIC','AVAILABLE','API_PENDING','AUTH_REQUIRED','NOT_APPLICABLE','UNCONFIRMED')),
 CHECK(payload#>>'{capability,state}'<>'AVAILABLE'
  OR (binding_id IS NOT NULL AND authorization_id IS NOT NULL))
);
ALTER TABLE configuration_field_assertions ENABLE ROW LEVEL SECURITY;
ALTER TABLE configuration_field_assertions FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON configuration_field_assertions TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY current_configuration_scope ON configuration_field_assertions FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND (
  access.configuration_scope(account_id,shop_id,business_entity_id,'snapshot.read')
  OR access.configuration_scope(account_id,shop_id,business_entity_id,'plan.write')));
GRANT SELECT ON configuration_field_assertions TO app_login;
