CREATE TABLE content_asset_account_links (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,shop_id uuid NOT NULL,asset_id uuid NOT NULL,
 account_id uuid NOT NULL,product_line varchar(20) NOT NULL
 CHECK(product_line IN ('adq','shop','beans','qianchuan','juguang')),
 revision integer NOT NULL CHECK(revision>0),active boolean NOT NULL,
 snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
 asset_version varchar(256) NOT NULL,asset_sha256 varchar(64) NOT NULL,
 linked_by uuid NOT NULL REFERENCES users(id),linked_at timestamptz NOT NULL,
 cancelled_by uuid REFERENCES users(id),cancelled_at timestamptz,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,asset_id,product_line,account_id),
 FOREIGN KEY(tenant_id,asset_id,shop_id) REFERENCES content_assets(tenant_id,id,shop_id),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id),
 CHECK((active AND cancelled_at IS NULL AND cancelled_by IS NULL) OR
       (NOT active AND cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL))
);
CREATE TABLE content_asset_account_link_events (
 tenant_id bigint NOT NULL,link_id uuid NOT NULL,revision integer NOT NULL,
 actor_id uuid NOT NULL REFERENCES users(id),action varchar(10) NOT NULL CHECK(action IN ('LINKED','CANCELLED')),
 snapshot jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,link_id,revision),
 FOREIGN KEY(tenant_id,link_id) REFERENCES content_asset_account_links(tenant_id,id)
);
ALTER TABLE content_asset_account_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_asset_account_links FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON content_asset_account_links TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON content_asset_account_links FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
CREATE POLICY tenant_insert ON content_asset_account_links FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
GRANT SELECT,INSERT ON content_asset_account_links TO app_login;
ALTER TABLE content_asset_account_link_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_asset_account_link_events FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON content_asset_account_link_events TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON content_asset_account_link_events FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
CREATE POLICY tenant_insert ON content_asset_account_link_events FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
GRANT SELECT,INSERT ON content_asset_account_link_events TO app_login;
CREATE POLICY tenant_update ON content_asset_account_links FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
GRANT UPDATE ON content_asset_account_links TO app_login;

CREATE FUNCTION access.content_account_link_target(p_account uuid,p_shop uuid,p_line text)
RETURNS TABLE(native_id text,name text,entity_id uuid)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT a.native_id::text,a.name::text,a.entity_id
 FROM public.ad_accounts a
 JOIN public.shops shop ON (shop.tenant_id,shop.id)=(a.tenant_id,p_shop)
 JOIN public.entity_links link ON (link.tenant_id,link.account_id,link.shop_id)=(a.tenant_id,a.id,shop.id)
 JOIN public.business_entities e ON (e.tenant_id,e.id)=(a.tenant_id,a.entity_id)
 JOIN (VALUES ('adq','tencent','adq','adq'),('shop','tencent','shop','wxpromo'),
              ('beans','beans','beans','beans'),('qianchuan','ocean','qianchuan','qianchuan'),
              ('juguang','xhs','juguang','juguang')) AS descriptor(line,provider,product,connector)
 ON descriptor.line=p_line AND a.provider=descriptor.provider AND a.product_line=descriptor.product
 JOIN public.authorization_account_links al ON (al.tenant_id,al.account_id)=(a.tenant_id,a.id)
 JOIN public.provider_authorizations p ON (p.tenant_id,p.id)=(al.tenant_id,al.authorization_id)
 WHERE a.tenant_id=public.current_tenant_id() AND public.current_actor_id() IS NOT NULL
 AND a.id=p_account AND a.status='AUTHORIZED' AND a.deleted_at IS NULL
 AND shop.deleted_at IS NULL AND shop.entity_id=a.entity_id
 AND e.deleted_at IS NULL AND e.status='ACTIVE'
 AND access.configuration_scope(a.id,shop.id,a.entity_id,'content.write')
 AND (access.configuration_scope(a.id,shop.id,a.entity_id,'snapshot.read')
      OR access.configuration_scope(a.id,shop.id,a.entity_id,'plan.write'))
 AND al.native_id_snapshot=a.native_id AND p.connector_id=descriptor.connector
 AND p.provider=descriptor.provider AND p.product_line=descriptor.product
 AND p.status='ACTIVE' AND p.expires_at>clock_timestamp() AND cardinality(p.scopes)>0
 ORDER BY p.id LIMIT 1 FOR SHARE OF a,shop,link,e,p,al;
$$;
REVOKE ALL ON FUNCTION access.content_account_link_target(uuid,uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.content_account_link_target(uuid,uuid,text) TO app_login;
