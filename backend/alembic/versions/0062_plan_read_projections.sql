ALTER TABLE sdk_read_catalogs DROP CONSTRAINT sdk_read_catalogs_profile_check;
ALTER TABLE sdk_read_catalogs ADD CONSTRAINT sdk_read_catalogs_profile_check CHECK(profile IN (
 'ADQ_CAMPAIGNS_V1_3_READ','ADQ_FUNDS_V1_3_READ','ADQ_DAILY_REPORTS_V1_3_READ',
 'ADQ_HOURLY_REPORTS_V1_3_READ','ADQ_ADGROUPS_V1_3_READ'));
ALTER TABLE capability_route_sources DROP CONSTRAINT capability_route_sources_profile_check;
ALTER TABLE capability_route_sources ADD CONSTRAINT capability_route_sources_profile_check CHECK(profile IN (
 'ADQ_CAMPAIGNS_V1_3_READ','ADQ_FUNDS_V1_3_READ','ADQ_DAILY_REPORTS_V1_3_READ',
 'ADQ_HOURLY_REPORTS_V1_3_READ','ADQ_ADGROUPS_V1_3_READ'));
CREATE TABLE adq_plan_pages (
 tenant_id bigint NOT NULL,source_id uuid NOT NULL,run_id uuid NOT NULL,
 page_no integer NOT NULL,outbound_id uuid NOT NULL,account_id uuid NOT NULL,shop_id uuid NOT NULL,
 authorization_id uuid NOT NULL,authorization_version bigint NOT NULL CHECK(authorization_version>0),
 binding_version integer NOT NULL CHECK(binding_version>0),
 level varchar(10) NOT NULL CHECK(level IN ('CAMPAIGN','ADGROUP')),
 page_size integer NOT NULL,total_number integer NOT NULL CHECK(total_number BETWEEN 0 AND 10000),
 total_page integer NOT NULL,fetched_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,run_id,page_no),
 FOREIGN KEY(tenant_id,authorization_id) REFERENCES provider_authorizations(tenant_id,id),
 FOREIGN KEY(tenant_id,source_id,account_id) REFERENCES sync_sources(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id) REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
CREATE TABLE adq_plan_observations (
 tenant_id bigint NOT NULL,run_id uuid NOT NULL,page_no integer NOT NULL,
 native_id varchar(128) NOT NULL,observation jsonb NOT NULL CHECK(jsonb_typeof(observation)='object'),
 PRIMARY KEY(tenant_id,run_id,native_id),
 FOREIGN KEY(tenant_id,run_id,page_no) REFERENCES adq_plan_pages(tenant_id,run_id,page_no)
);
CREATE TABLE adq_plan_measure_proofs (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,source_id uuid NOT NULL,account_id uuid NOT NULL,
 version integer NOT NULL CHECK(version>0),payload jsonb NOT NULL,
 checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL CHECK(expires_at>checked_at),
 UNIQUE(tenant_id,source_id,version),
 FOREIGN KEY(tenant_id,source_id,account_id) REFERENCES sync_sources(tenant_id,id,account_id)
);
ALTER TABLE adq_plan_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE adq_plan_pages FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON adq_plan_pages TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON adq_plan_pages FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
GRANT SELECT ON adq_plan_pages TO app_login;
CREATE POLICY tenant_insert ON adq_plan_pages FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
GRANT INSERT ON adq_plan_pages TO app_login;
ALTER TABLE adq_plan_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE adq_plan_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON adq_plan_observations TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON adq_plan_observations FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
GRANT SELECT ON adq_plan_observations TO app_login;
CREATE POLICY tenant_insert ON adq_plan_observations FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
GRANT INSERT ON adq_plan_observations TO app_login;
ALTER TABLE adq_plan_measure_proofs ENABLE ROW LEVEL SECURITY;
ALTER TABLE adq_plan_measure_proofs FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON adq_plan_measure_proofs TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON adq_plan_measure_proofs FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
GRANT SELECT ON adq_plan_measure_proofs TO app_login;
