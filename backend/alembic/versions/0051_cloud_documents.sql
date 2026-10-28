ALTER TABLE commerce_products ADD CONSTRAINT commerce_product_shop_key UNIQUE(tenant_id,id,shop_id);
CREATE TABLE content_documents (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,shop_id uuid NOT NULL,product_id uuid,
 parent_id uuid,kind varchar(10) NOT NULL CHECK(kind IN ('planning','scripts','graphics','videos','editing')),
 revision integer NOT NULL CHECK(revision>0),payload jsonb NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),deleted_at timestamptz,
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,shop_id) REFERENCES shops(tenant_id,id),
 FOREIGN KEY(tenant_id,product_id,shop_id) REFERENCES commerce_products(tenant_id,id,shop_id),
 FOREIGN KEY(tenant_id,parent_id) REFERENCES content_documents(tenant_id,id)
);
CREATE TABLE content_document_references (
 tenant_id bigint NOT NULL,document_id uuid NOT NULL,kind text NOT NULL CHECK(kind IN ('RUN','GENERATION')),
 reference_id uuid NOT NULL,active boolean NOT NULL DEFAULT true,
 PRIMARY KEY(tenant_id,document_id,kind,reference_id),
 FOREIGN KEY(tenant_id,document_id) REFERENCES content_documents(tenant_id,id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['content_documents','content_document_references'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('CREATE POLICY tenant_update ON %I FOR UPDATE TO app_login USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON %I TO app_login',item);
 END LOOP;
END $$;

CREATE TABLE content_document_versions (
 tenant_id bigint NOT NULL,document_id uuid NOT NULL,revision integer NOT NULL CHECK(revision>0),
 payload jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,document_id,revision),
 FOREIGN KEY(tenant_id,document_id) REFERENCES content_documents(tenant_id,id)
);
ALTER TABLE content_document_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_document_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON content_document_versions TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON content_document_versions FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
CREATE POLICY tenant_insert ON content_document_versions FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
GRANT SELECT,INSERT ON content_document_versions TO app_login;
