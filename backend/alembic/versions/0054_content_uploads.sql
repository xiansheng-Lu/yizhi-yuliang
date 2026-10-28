CREATE TABLE content_uploads (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,shop_id uuid NOT NULL,product_id uuid,document_id uuid,
 actor_id uuid NOT NULL REFERENCES users(id),client_request_key uuid NOT NULL,request_digest varchar(64) NOT NULL,
 name varchar(200) NOT NULL,kind varchar(8) NOT NULL CHECK(kind IN ('image','video','audio','text')),
 mime varchar(80) NOT NULL,size_bytes bigint NOT NULL CHECK(size_bytes>0 AND size_bytes<=104857600),
 sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 object_key varchar(300) NOT NULL UNIQUE,bucket varchar(63) NOT NULL,
 state varchar(12) NOT NULL CHECK(state IN ('PENDING','VERIFYING','CONFIRMED')),
 verify_token uuid,verify_until timestamptz,last_error varchar(60),
 expires_at timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),confirmed_at timestamptz,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,actor_id,client_request_key),
 FOREIGN KEY(tenant_id,shop_id) REFERENCES shops(tenant_id,id),
 FOREIGN KEY(tenant_id,product_id,shop_id) REFERENCES commerce_products(tenant_id,id,shop_id),
 FOREIGN KEY(tenant_id,document_id) REFERENCES content_documents(tenant_id,id)
);
CREATE TABLE content_assets (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,shop_id uuid NOT NULL,product_id uuid,document_id uuid,
 name varchar(200) NOT NULL,kind varchar(8) NOT NULL CHECK(kind IN ('image','video','audio','text')),
 mime varchar(80) NOT NULL,size_bytes bigint NOT NULL CHECK(size_bytes>0),
 sha256 varchar(64) NOT NULL,object_key varchar(300) NOT NULL,bucket varchar(63) NOT NULL,
 object_version varchar(256) NOT NULL CHECK(object_version NOT IN ('','null')),
 duration numeric(12,3),duration_state varchar(16) NOT NULL CHECK(duration_state IN ('NOT_APPLICABLE','UNCONFIRMED','VERIFIED')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),deleted_at timestamptz,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,id,shop_id),UNIQUE(bucket,object_key,object_version),
 FOREIGN KEY(tenant_id,id) REFERENCES content_uploads(tenant_id,id),
 FOREIGN KEY(tenant_id,shop_id) REFERENCES shops(tenant_id,id),
 FOREIGN KEY(tenant_id,product_id,shop_id) REFERENCES commerce_products(tenant_id,id,shop_id),
 FOREIGN KEY(tenant_id,document_id) REFERENCES content_documents(tenant_id,id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['content_uploads','content_assets'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT,INSERT ON %I TO app_login',item);
 END LOOP;
 CREATE POLICY tenant_update ON content_uploads FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
 GRANT UPDATE ON content_uploads TO app_login;
END $$;
