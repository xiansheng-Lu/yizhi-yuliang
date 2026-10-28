ALTER TABLE catalog_imports DROP CONSTRAINT catalog_imports_schema_version_check;
ALTER TABLE catalog_imports ADD CONSTRAINT catalog_imports_schema_version_check
 CHECK(schema_version IN ('platform-catalog-v1','official-sdk-read-catalog-v1'));
CREATE TABLE sdk_read_catalogs (
 catalog_sha256 varchar(64) PRIMARY KEY REFERENCES catalog_imports(sha256),
 base_catalog_sha256 varchar(64) NOT NULL REFERENCES catalog_imports(sha256),
 profile varchar(40) NOT NULL CHECK(profile IN ('ADQ_CAMPAIGNS_V1_3_READ','ADQ_FUNDS_V1_3_READ')),
 sdk_revision varchar(40) NOT NULL CHECK(sdk_revision='81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9'),
 active boolean NOT NULL DEFAULT true,created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE sdk_read_catalogs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sdk_read_catalogs FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON sdk_read_catalogs TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY metadata_read ON sdk_read_catalogs FOR SELECT TO app_login
 USING(public.current_tenant_id() IS NOT NULL);
GRANT SELECT ON sdk_read_catalogs TO app_login;
CREATE FUNCTION access.catalog_is_current(p_sha text,p_kind text) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.catalog_head h WHERE h.catalog_sha256=p_sha)
 OR (p_kind='READ' AND EXISTS(SELECT 1 FROM public.sdk_read_catalogs s
  JOIN public.catalog_head h ON h.catalog_sha256=s.base_catalog_sha256
  WHERE s.catalog_sha256=p_sha AND s.active))
$$;
REVOKE ALL ON FUNCTION access.catalog_is_current(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION access.catalog_is_current(text,text) TO app_login;

CREATE TABLE capability_route_sources (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,binding_id uuid NOT NULL UNIQUE,account_id uuid NOT NULL,
 profile varchar(40) NOT NULL CHECK(profile IN ('ADQ_CAMPAIGNS_V1_3_READ','ADQ_FUNDS_V1_3_READ')),
 payload jsonb NOT NULL,checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 CHECK(expires_at>checked_at),
 CHECK((jsonb_typeof(payload)='object' AND payload->>'id'=id::text AND payload->>'profile'=profile) IS TRUE)
);
ALTER TABLE capability_route_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE capability_route_sources FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON capability_route_sources TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON capability_route_sources FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id());
GRANT SELECT ON capability_route_sources TO app_login;
