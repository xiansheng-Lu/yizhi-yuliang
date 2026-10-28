CREATE TABLE shop_compass_observations (
 tenant_id bigint NOT NULL,account_id uuid NOT NULL,shop_id uuid NOT NULL,
 source_id uuid NOT NULL,run_id uuid NOT NULL,page_no integer NOT NULL,outbound_id uuid NOT NULL,
 report_date date NOT NULL,observation jsonb NOT NULL,fetched_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,run_id,page_no),
 FOREIGN KEY(tenant_id,source_id,account_id) REFERENCES sync_sources(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id) REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id),
 CHECK(jsonb_typeof(observation)='object')
);
ALTER TABLE shop_compass_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE shop_compass_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON shop_compass_observations TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON shop_compass_observations FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
CREATE POLICY tenant_insert ON shop_compass_observations FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
GRANT SELECT,INSERT ON shop_compass_observations TO app_login;
CREATE TABLE shop_compass_contracts (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,account_id uuid NOT NULL,binding_id uuid NOT NULL,shop_id uuid NOT NULL,
 payload jsonb NOT NULL,checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,active boolean NOT NULL DEFAULT true,
 UNIQUE(tenant_id,id),CHECK(expires_at>checked_at),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id)
);
CREATE UNIQUE INDEX shop_compass_contract_current ON shop_compass_contracts(tenant_id,binding_id) WHERE active;
ALTER TABLE shop_compass_contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE shop_compass_contracts FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON shop_compass_contracts TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON shop_compass_contracts FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
GRANT SELECT ON shop_compass_contracts TO app_login;
ALTER TABLE shop_compass_observations ADD COLUMN contract_id uuid;
ALTER TABLE shop_compass_observations ADD COLUMN fact_decisions jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE shop_compass_observations ADD FOREIGN KEY(tenant_id,contract_id) REFERENCES shop_compass_contracts(tenant_id,id);
CREATE TABLE shop_compass_runs (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,account_id uuid NOT NULL,binding_id uuid NOT NULL,
 authorization_id uuid NOT NULL,authorization_version bigint NOT NULL CHECK(authorization_version>0),
 report_date date NOT NULL,
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,authorization_id) REFERENCES provider_authorizations(tenant_id,id)
);
ALTER TABLE shop_compass_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE shop_compass_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON shop_compass_runs TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON shop_compass_runs FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
CREATE POLICY tenant_insert ON shop_compass_runs FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
GRANT SELECT,INSERT ON shop_compass_runs TO app_login;
