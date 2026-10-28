CREATE TABLE generation_jobs (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,actor_id uuid NOT NULL,membership_id uuid NOT NULL,
 shop_id uuid NOT NULL,product_id uuid NOT NULL,document_id uuid NOT NULL,document_revision integer NOT NULL,
 run_id uuid NOT NULL,step_id uuid NOT NULL,step_attempt integer NOT NULL,expected_run_revision integer NOT NULL,
 provider varchar(40) NOT NULL,kind varchar(16) NOT NULL CHECK(kind IN ('IMAGE','GRAPHIC','VIDEO','VIDEO_EDIT','SCRIPT')),
 request_key uuid NOT NULL,client_hash varchar(64) NOT NULL,payload_hash varchar(64) NOT NULL,
 request_fingerprint varchar(64) NOT NULL,quote_id uuid NOT NULL,permit_id uuid NOT NULL,
 status varchar(20) NOT NULL CHECK(status IN ('QUEUED','RUNNING','WAITING_PROVIDER','WAITING_REVIEW','SUCCEEDED','FAILED','CANCELLED')),
 submit_state varchar(16) NOT NULL CHECK(submit_state IN ('NOT_SENT','SENT','ACKNOWLEDGED','UNKNOWN')),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),parameters jsonb NOT NULL,assets_snapshot jsonb NOT NULL,
 external_job_id varchar(128),result_asset_id uuid,estimated_cost_minor bigint,currency varchar(3) NOT NULL CHECK(currency IN ('CNY','USD')),
 actual_cost_minor bigint,billing_state varchar(8) NOT NULL DEFAULT 'UNKNOWN' CHECK(billing_state IN ('UNKNOWN','KNOWN')),
 error_code varchar(80),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,request_key),UNIQUE(provider,request_key),
 FOREIGN KEY(tenant_id,membership_id,actor_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,document_id,shop_id,product_id) REFERENCES content_documents(tenant_id,id,shop_id,product_id),
 FOREIGN KEY(tenant_id,document_id,document_revision) REFERENCES content_document_versions(tenant_id,document_id,revision),
 FOREIGN KEY(tenant_id,run_id,shop_id,product_id) REFERENCES workflow_runs(tenant_id,id,shop_id,product_id),
 FOREIGN KEY(tenant_id,quote_id) REFERENCES ai_cost_quotes(tenant_id,id),
 FOREIGN KEY(tenant_id,permit_id) REFERENCES ai_cost_permits(tenant_id,id),
 FOREIGN KEY(tenant_id,result_asset_id) REFERENCES content_assets(tenant_id,id),
 CHECK(((billing_state='UNKNOWN' AND actual_cost_minor IS NULL) OR
        (billing_state='KNOWN' AND actual_cost_minor IS NOT NULL AND actual_cost_minor>=0)) IS TRUE),
 CHECK(estimated_cost_minor IS NULL OR estimated_cost_minor>=0)
);
ALTER TABLE workflow_run_steps ADD CONSTRAINT workflow_step_run_key UNIQUE(tenant_id,id,run_id);
ALTER TABLE generation_jobs ADD CONSTRAINT generation_step_fk
 FOREIGN KEY(tenant_id,step_id,run_id) REFERENCES workflow_run_steps(tenant_id,id,run_id);
CREATE UNIQUE INDEX generation_one_inflight_step ON generation_jobs(tenant_id,step_id)
 WHERE status IN ('QUEUED','RUNNING','WAITING_PROVIDER','WAITING_REVIEW');
CREATE TABLE generation_dispatches (
 tenant_id bigint NOT NULL,job_id uuid NOT NULL,attempt_no integer NOT NULL CHECK(attempt_no=1),
 request_key uuid NOT NULL,payload_hash varchar(64) NOT NULL,sent_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,job_id),FOREIGN KEY(tenant_id,job_id) REFERENCES generation_jobs(tenant_id,id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['generation_jobs','generation_dispatches'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  IF item='generation_jobs' THEN
   EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id())',item);
  END IF;
 END LOOP;
END $$;
CREATE POLICY tenant_read ON generation_dispatches FOR SELECT TO app_login USING(
 tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM generation_jobs job
 WHERE (job.tenant_id,job.id,job.request_key,job.payload_hash)=
 (generation_dispatches.tenant_id,generation_dispatches.job_id,generation_dispatches.request_key,generation_dispatches.payload_hash)
 AND job.actor_id=public.current_actor_id()));
CREATE POLICY tenant_insert ON generation_jobs FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
CREATE POLICY tenant_update ON generation_jobs FOR UPDATE TO app_login USING(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id()) WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
CREATE POLICY tenant_insert ON generation_dispatches FOR INSERT TO app_login WITH CHECK(
 tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM generation_jobs job
 JOIN ai_cost_permits permit ON (permit.tenant_id,permit.id,permit.actor_id,permit.quote_id,permit.request_key)=
 (job.tenant_id,job.permit_id,job.actor_id,job.quote_id,job.request_key)
 WHERE (job.tenant_id,job.id,job.request_key,job.payload_hash)=
 (generation_dispatches.tenant_id,generation_dispatches.job_id,generation_dispatches.request_key,generation_dispatches.payload_hash)
 AND job.actor_id=public.current_actor_id() AND job.status='QUEUED' AND job.submit_state='NOT_SENT'
 AND permit.kind='GENERATION' AND permit.state='IN_FLIGHT'));
GRANT SELECT,INSERT ON generation_jobs,generation_dispatches TO app_login;
GRANT UPDATE(status,submit_state,version,external_job_id,result_asset_id,actual_cost_minor,billing_state,error_code,updated_at) ON generation_jobs TO app_login;

CREATE TABLE generation_price_sources (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,provider varchar(40) NOT NULL,kind varchar(16) NOT NULL,
 price_id uuid NOT NULL,unit_semantic varchar(20) NOT NULL CHECK(unit_semantic='ONE_REQUEST_JOB'),
 proof_ref varchar(180) NOT NULL CHECK(length(proof_ref)>0),
 source_sha256 varchar(64) NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL CHECK(expires_at>checked_at),
 FOREIGN KEY(tenant_id,price_id) REFERENCES ai_price_versions(tenant_id,id),
 UNIQUE(tenant_id,provider,kind,price_id)
);
ALTER TABLE generation_price_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE generation_price_sources FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON generation_price_sources TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON generation_price_sources FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
GRANT SELECT ON generation_price_sources TO app_login;
