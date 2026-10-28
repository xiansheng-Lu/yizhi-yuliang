CREATE TABLE content_imports (
 tenant_id bigint NOT NULL,actor_id uuid NOT NULL REFERENCES users(id),shop_id uuid NOT NULL,
 import_key varchar(100) NOT NULL,manifest_digest varchar(64) NOT NULL,
 source_revision bigint NOT NULL CHECK(source_revision>=0),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,actor_id,shop_id,import_key),
 FOREIGN KEY(tenant_id,shop_id) REFERENCES shops(tenant_id,id)
);
CREATE TABLE content_import_entries (
 tenant_id bigint NOT NULL,actor_id uuid NOT NULL,shop_id uuid NOT NULL,import_key varchar(100) NOT NULL,
 local_id varchar(128) NOT NULL,cloud_id uuid NOT NULL,receipt jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,actor_id,shop_id,import_key,local_id),
 UNIQUE(tenant_id,cloud_id),
 FOREIGN KEY(tenant_id,actor_id,shop_id,import_key) REFERENCES content_imports(tenant_id,actor_id,shop_id,import_key),
 FOREIGN KEY(tenant_id,cloud_id) REFERENCES content_documents(tenant_id,id)
);
ALTER TABLE content_imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_imports FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON content_imports TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY self_read ON content_imports FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
CREATE POLICY self_insert ON content_imports FOR INSERT TO app_login
 WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
GRANT SELECT,INSERT ON content_imports TO app_login;
ALTER TABLE content_import_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_import_entries FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON content_import_entries TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY self_read ON content_import_entries FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
CREATE POLICY self_insert ON content_import_entries FOR INSERT TO app_login
 WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
GRANT SELECT,INSERT ON content_import_entries TO app_login;
