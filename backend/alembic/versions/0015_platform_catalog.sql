CREATE TABLE catalog_imports (
 sha256 varchar(64) PRIMARY KEY CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 schema_version varchar(40) NOT NULL CHECK(schema_version='platform-catalog-v1'),
 normalized_sha256 varchar(64) NOT NULL CHECK(normalized_sha256 ~ '^[0-9a-f]{64}$'),
 source_manifest jsonb NOT NULL CHECK(jsonb_typeof(source_manifest)='array'),
 record_count integer NOT NULL CHECK(record_count>0),
 empty_paths integer NOT NULL CHECK(empty_paths>=0 AND empty_paths<=record_count),
 duplicate_routes integer NOT NULL CHECK(duplicate_routes>=0),
 imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE endpoint_catalog (
 catalog_sha256 varchar(64) NOT NULL REFERENCES catalog_imports(sha256),
 catalog_id varchar(20) NOT NULL CHECK(catalog_id ~ '^[0-9a-f]{20}$'),
 provider varchar(20) NOT NULL CHECK(provider IN ('tencent','ocean','wxshop','xhs')),
 source_file text NOT NULL, operation text NOT NULL,
 normalized_path text CHECK(normalized_path IS NULL OR normalized_path ~ '^/[^[:space:]]*$'),
 http_method varchar(8) NOT NULL CHECK(http_method IN ('UNKNOWN','GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS')),
 scope_hint jsonb NOT NULL CHECK(jsonb_typeof(scope_hint)='array'),
 doc_status varchar(20) NOT NULL CHECK(doc_status IN ('SOURCE_VERIFIED','LISTED','UNCONFIRMED')),
 record_json jsonb NOT NULL CHECK(jsonb_typeof(record_json)='object'),
 route_enabled boolean NOT NULL DEFAULT false CHECK(route_enabled=false),
 PRIMARY KEY(catalog_sha256,catalog_id)
);
CREATE INDEX ix_endpoint_catalog_provider ON endpoint_catalog(catalog_sha256,provider);
CREATE TABLE catalog_head (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton=true),
 catalog_sha256 varchar(64) NOT NULL REFERENCES catalog_imports(sha256)
);
CREATE TABLE catalog_connectors (
 connector_id varchar(20) PRIMARY KEY,
 name varchar(80) NOT NULL,
 reference_provider varchar(20) CHECK(reference_provider IN ('tencent','ocean','wxshop','xhs')),
 binding_status varchar(20) NOT NULL DEFAULT 'UNCONFIRMED' CHECK(binding_status='UNCONFIRMED')
);
INSERT INTO catalog_connectors(connector_id,name,reference_provider) VALUES
 ('adq','腾讯广告 ADQ','tencent'), ('wxpromo','微信小店推广','tencent'),
 ('beans','微信豆',NULL), ('wxshop','微信小店经营','wxshop'),
 ('qianchuan','巨量千川','ocean'), ('douyinshop','抖音小店','ocean'),
 ('juguang','小红书聚光','xhs'), ('redshop','小红书店铺','xhs');
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['catalog_imports','endpoint_catalog','catalog_head','catalog_connectors'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY catalog_maintenance ON public.%I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY catalog_read ON public.%I FOR SELECT TO app_login USING(public.current_tenant_id() IS NOT NULL)',item);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,app_login,app_auth',item);
  EXECUTE format('GRANT SELECT ON public.%I TO app_login',item);
 END LOOP;
END $$;
