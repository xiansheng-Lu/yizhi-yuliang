ALTER TABLE sync_sources ADD CONSTRAINT ux_sync_source_account UNIQUE(tenant_id,id,account_id);
CREATE TABLE adq_finance_pages (
 tenant_id bigint NOT NULL,account_id uuid NOT NULL,source_id uuid NOT NULL,run_id uuid NOT NULL,
 page_no integer NOT NULL,outbound_id uuid NOT NULL,kind varchar(12) NOT NULL
 CHECK(kind IN ('FUNDS','OPERATIONS')),rows_digest varchar(64) NOT NULL,
 total_number bigint NOT NULL CHECK(total_number>=0),total_page integer NOT NULL CHECK(total_page>=0),
 fetched_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,run_id,page_no),UNIQUE(tenant_id,run_id,page_no,kind,account_id),
 FOREIGN KEY(tenant_id,source_id,account_id) REFERENCES sync_sources(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,source_id,run_id) REFERENCES sync_runs(tenant_id,source_id,id),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id)
 REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
CREATE TABLE adq_fund_observations (
 tenant_id bigint NOT NULL,account_id uuid NOT NULL,run_id uuid NOT NULL,page_no integer NOT NULL,
 kind varchar(12) NOT NULL DEFAULT 'FUNDS' CHECK(kind='FUNDS'),fund_type varchar(80) NOT NULL,
 native_balance varchar(20),native_realtime_cost varchar(20),fund_status varchar(80),
 unit_state varchar(20) NOT NULL DEFAULT 'UNCONFIRMED' CHECK(unit_state='UNCONFIRMED'),
 PRIMARY KEY(tenant_id,run_id,fund_type),
 CHECK(native_balance IS NULL OR native_balance ~ '^-?[0-9]+$'),
 CHECK(native_realtime_cost IS NULL OR native_realtime_cost ~ '^-?[0-9]+$'),
 FOREIGN KEY(tenant_id,run_id,page_no,kind,account_id)
 REFERENCES adq_finance_pages(tenant_id,run_id,page_no,kind,account_id)
);
CREATE TABLE adq_operation_observations (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,account_id uuid NOT NULL,
 run_id uuid NOT NULL,page_no integer NOT NULL,row_no integer NOT NULL CHECK(row_no>=0),
 kind varchar(12) NOT NULL DEFAULT 'OPERATIONS' CHECK(kind='OPERATIONS'),
 operation_object_type varchar(80) NOT NULL,operation_object_id varchar(19) NOT NULL,
 operation_action varchar(80) NOT NULL,operator_platform varchar(80) NOT NULL,
 native_created_time varchar(20),native_log_id text CHECK(native_log_id IS NULL),
 time_unit varchar(20) NOT NULL DEFAULT 'UNCONFIRMED' CHECK(time_unit='UNCONFIRMED'),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,run_id,page_no,row_no),
 FOREIGN KEY(tenant_id,run_id,page_no,kind,account_id)
 REFERENCES adq_finance_pages(tenant_id,run_id,page_no,kind,account_id)
);
CREATE TABLE adq_wallet_measure_proofs (
 id uuid PRIMARY KEY,sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 tenant_id bigint NOT NULL,source_id uuid NOT NULL,metric varchar(20) NOT NULL
 CHECK(metric IN ('BALANCE','IN_TRANSIT','UNSETTLED','CREDIT','REALTIME_COST')),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 active boolean NOT NULL DEFAULT true,CHECK(expires_at>checked_at),
 FOREIGN KEY(tenant_id,source_id) REFERENCES sync_sources(tenant_id,id),
 CHECK((payload->>'id'=id::text AND payload->>'source_id'=source_id::text
  AND payload->>'metric'=metric) IS TRUE)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['adq_finance_pages','adq_fund_observations','adq_operation_observations','adq_wallet_measure_proofs'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
 FOREACH item IN ARRAY ARRAY['adq_finance_pages','adq_fund_observations','adq_operation_observations'] LOOP
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT INSERT ON %I TO app_login',item);
 END LOOP;
END $$;
