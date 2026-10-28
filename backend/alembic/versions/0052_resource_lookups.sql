CREATE TABLE resource_lookup_contexts (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,account_id uuid NOT NULL,binding_id uuid NOT NULL,
 context_key text NOT NULL,context_digest varchar(64) NOT NULL,payload jsonb NOT NULL,checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 active boolean NOT NULL DEFAULT true,UNIQUE(tenant_id,id),CHECK(expires_at>checked_at),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id)
);
CREATE UNIQUE INDEX resource_lookup_current ON resource_lookup_contexts(tenant_id,context_digest) WHERE active;
CREATE TABLE resource_lookup_pages (
 tenant_id bigint NOT NULL,account_id uuid NOT NULL,source_id uuid NOT NULL,run_id uuid NOT NULL,
 page_no integer NOT NULL,outbound_id uuid NOT NULL,proof_id uuid NOT NULL,
 items jsonb NOT NULL,total_number bigint NOT NULL,total_page integer NOT NULL,fetched_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,run_id,page_no),
 FOREIGN KEY(tenant_id,source_id,account_id) REFERENCES sync_sources(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id) REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id),
 FOREIGN KEY(tenant_id,proof_id) REFERENCES resource_lookup_contexts(tenant_id,id),
 CHECK(jsonb_typeof(items)='array')
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['resource_lookup_contexts','resource_lookup_pages'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE POLICY tenant_insert ON resource_lookup_pages FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
GRANT INSERT ON resource_lookup_pages TO app_login;
