ALTER TABLE sdk_read_catalogs DROP CONSTRAINT sdk_read_catalogs_profile_check;
ALTER TABLE sdk_read_catalogs ADD CONSTRAINT sdk_read_catalogs_profile_check CHECK(profile IN (
 'ADQ_CAMPAIGNS_V1_3_READ','ADQ_FUNDS_V1_3_READ','ADQ_DAILY_REPORTS_V1_3_READ','ADQ_HOURLY_REPORTS_V1_3_READ'));
ALTER TABLE capability_route_sources DROP CONSTRAINT capability_route_sources_profile_check;
ALTER TABLE capability_route_sources ADD CONSTRAINT capability_route_sources_profile_check CHECK(profile IN (
 'ADQ_CAMPAIGNS_V1_3_READ','ADQ_FUNDS_V1_3_READ','ADQ_DAILY_REPORTS_V1_3_READ','ADQ_HOURLY_REPORTS_V1_3_READ'));
CREATE TABLE adq_report_contracts (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,account_id uuid NOT NULL,binding_id uuid NOT NULL,
 shop_id uuid NOT NULL,kind varchar(6) NOT NULL CHECK(kind IN ('DAILY','HOURLY')),
 payload jsonb NOT NULL,checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 active boolean NOT NULL DEFAULT true,UNIQUE(tenant_id,id),CHECK(expires_at>checked_at),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id)
);
CREATE UNIQUE INDEX adq_report_contract_current ON adq_report_contracts(tenant_id,binding_id)
 WHERE active;
CREATE TABLE adq_report_batches (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,user_id uuid NOT NULL REFERENCES users(id),
 account_id uuid NOT NULL,shop_id uuid NOT NULL,binding_id uuid NOT NULL,
 authorization_id uuid NOT NULL,authorization_version bigint NOT NULL CHECK(authorization_version>0),
 contract_id uuid,request_digest varchar(64) NOT NULL,kind varchar(6) NOT NULL CHECK(kind IN ('DAILY','HOURLY')),
 time_line varchar(16) NOT NULL CHECK(time_line IN ('REQUEST_TIME','REPORTING_TIME','ACTIVE_TIME')),
 start_date date NOT NULL,end_date date NOT NULL,confirmed_through date,
 state varchar(15) NOT NULL CHECK(state IN ('RUNNING','PARTIAL','SUCCESS','RATE_LIMITED','FAILED')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id),
 FOREIGN KEY(tenant_id,authorization_id) REFERENCES provider_authorizations(tenant_id,id),
 FOREIGN KEY(tenant_id,contract_id) REFERENCES adq_report_contracts(tenant_id,id),
 CHECK(end_date>=start_date AND end_date-start_date<31),
 CHECK(confirmed_through IS NULL OR confirmed_through BETWEEN start_date AND end_date)
);
CREATE TABLE adq_report_batch_days (
 tenant_id bigint NOT NULL,batch_id uuid NOT NULL,report_date date NOT NULL,
 run_id uuid NOT NULL UNIQUE,source_id uuid,state varchar(15) NOT NULL
 CHECK(state IN ('PENDING','RUNNING','PARTIAL','SUCCESS','RATE_LIMITED')),
 PRIMARY KEY(tenant_id,batch_id,report_date),
 FOREIGN KEY(tenant_id,batch_id) REFERENCES adq_report_batches(tenant_id,id),
 FOREIGN KEY(tenant_id,source_id) REFERENCES sync_sources(tenant_id,id)
);
CREATE TABLE adq_report_pages (
 tenant_id bigint NOT NULL,account_id uuid NOT NULL,source_id uuid NOT NULL,run_id uuid NOT NULL,
 page_no integer NOT NULL,outbound_id uuid NOT NULL,report_date date NOT NULL,
 kind varchar(6) NOT NULL CHECK(kind IN ('DAILY','HOURLY')),rows_digest varchar(64) NOT NULL,
 total_number bigint NOT NULL,total_page integer NOT NULL,fetched_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,run_id,page_no),
 FOREIGN KEY(tenant_id,source_id,account_id) REFERENCES sync_sources(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,source_id,run_id) REFERENCES sync_runs(tenant_id,source_id,id),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id)
 REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
CREATE TABLE adq_report_observations (
 tenant_id bigint NOT NULL,run_id uuid NOT NULL,page_no integer NOT NULL,row_no integer NOT NULL,
 native_key varchar(80) NOT NULL,raw_values jsonb NOT NULL,gaps jsonb NOT NULL,
 fact_decisions jsonb NOT NULL,contract_id uuid,PRIMARY KEY(tenant_id,run_id,page_no,row_no),
 UNIQUE(tenant_id,run_id,native_key),
 FOREIGN KEY(tenant_id,run_id,page_no) REFERENCES adq_report_pages(tenant_id,run_id,page_no),
 FOREIGN KEY(tenant_id,contract_id) REFERENCES adq_report_contracts(tenant_id,id),
 CHECK(jsonb_typeof(raw_values)='object' AND jsonb_typeof(gaps)='array' AND jsonb_typeof(fact_decisions)='array')
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['adq_report_contracts','adq_report_batches','adq_report_batch_days','adq_report_pages','adq_report_observations'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
 FOREACH item IN ARRAY ARRAY['adq_report_batches','adq_report_batch_days','adq_report_pages','adq_report_observations'] LOOP
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT INSERT ON %I TO app_login',item);
 END LOOP;
 FOREACH item IN ARRAY ARRAY['adq_report_batches','adq_report_batch_days'] LOOP
  EXECUTE format('CREATE POLICY tenant_update ON %I FOR UPDATE TO app_login USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id())',item);
 END LOOP;
END $$;
GRANT UPDATE(state,confirmed_through) ON adq_report_batches TO app_login;
GRANT UPDATE(state,source_id) ON adq_report_batch_days TO app_login;
