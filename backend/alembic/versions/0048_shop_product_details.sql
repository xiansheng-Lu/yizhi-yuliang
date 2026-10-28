CREATE TABLE commerce_products (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,shop_id uuid NOT NULL,account_id uuid NOT NULL,
 native_id varchar(20) NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,shop_id,account_id,native_id),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id)
);
CREATE TABLE commerce_skus (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,product_id uuid NOT NULL,native_id varchar(20) NOT NULL,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,product_id,native_id),
 FOREIGN KEY(tenant_id,product_id) REFERENCES commerce_products(tenant_id,id)
);
CREATE TABLE shop_product_detail_pages (
 tenant_id bigint NOT NULL,shop_id uuid NOT NULL,account_id uuid NOT NULL,
 source_id uuid NOT NULL,run_id uuid NOT NULL,page_no integer NOT NULL,outbound_id uuid NOT NULL,
 product_id varchar(20) NOT NULL,source_at timestamptz,fetched_at timestamptz NOT NULL,
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 PRIMARY KEY(tenant_id,run_id,page_no),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id),
 FOREIGN KEY(tenant_id,source_id,run_id) REFERENCES sync_runs(tenant_id,source_id,id),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id) REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
CREATE INDEX shop_product_detail_lookup ON shop_product_detail_pages(tenant_id,shop_id,account_id,product_id,sequence DESC);
CREATE TABLE shop_product_measure_proofs (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,shop_id uuid NOT NULL,account_id uuid NOT NULL,
 binding_id uuid NOT NULL,payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,active boolean NOT NULL DEFAULT true,
 CHECK(expires_at>checked_at),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id)
);
CREATE UNIQUE INDEX shop_product_measure_current ON shop_product_measure_proofs(tenant_id,shop_id,account_id,binding_id) WHERE active;
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['shop_product_detail_pages','shop_product_measure_proofs','commerce_products','commerce_skus'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
 FOREACH item IN ARRAY ARRAY['commerce_products','commerce_skus'] LOOP
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT INSERT ON %I TO app_login',item);
 END LOOP;
 CREATE POLICY tenant_insert ON shop_product_detail_pages FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
 GRANT INSERT ON shop_product_detail_pages TO app_login;
 GRANT USAGE,SELECT ON SEQUENCE shop_product_detail_pages_sequence_seq TO app_login;
END $$;
