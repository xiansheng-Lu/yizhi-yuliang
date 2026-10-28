ALTER TABLE workflow_runs ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK(revision>0);
ALTER TABLE workflow_runs ADD COLUMN updated_at timestamptz NOT NULL DEFAULT clock_timestamp();
ALTER TABLE workflow_run_steps ADD COLUMN attempt integer NOT NULL DEFAULT 1 CHECK(attempt>0);
ALTER TABLE workflow_run_steps ADD COLUMN current_candidate_id uuid;
ALTER TABLE workflow_run_steps ADD COLUMN document_id uuid;
ALTER TABLE workflow_run_steps ADD COLUMN skip_reason text;
ALTER TABLE workflow_run_steps ADD CONSTRAINT workflow_step_document_fk
 FOREIGN KEY(tenant_id,document_id) REFERENCES content_documents(tenant_id,id);
CREATE TABLE workflow_step_candidates (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,run_id uuid NOT NULL,step_code varchar(40) NOT NULL,
 attempt integer NOT NULL CHECK(attempt>0),document_id uuid NOT NULL,document_revision integer NOT NULL,
 payload jsonb NOT NULL,derive_kind text CHECK(derive_kind IN ('planning','scripts','graphics','videos','editing')),
 review_note text NOT NULL,created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,run_id,step_code,attempt),
 FOREIGN KEY(tenant_id,run_id,step_code) REFERENCES workflow_run_steps(tenant_id,run_id,step_code),
 FOREIGN KEY(tenant_id,document_id,document_revision) REFERENCES content_document_versions(tenant_id,document_id,revision)
);
ALTER TABLE workflow_run_steps ADD CONSTRAINT workflow_current_candidate_fk
 FOREIGN KEY(tenant_id,current_candidate_id) REFERENCES workflow_step_candidates(tenant_id,id);
CREATE TABLE workflow_step_actions (
 tenant_id bigint NOT NULL,run_id uuid NOT NULL,action_key varchar(160) NOT NULL,step_code varchar(40) NOT NULL,
 action text NOT NULL CHECK(action IN ('execute','accept','skip','redo')),request_hash text NOT NULL,
 result jsonb NOT NULL,reason text,created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,run_id,action_key),
 FOREIGN KEY(tenant_id,run_id,step_code) REFERENCES workflow_run_steps(tenant_id,run_id,step_code)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['workflow_step_candidates','workflow_step_actions'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT,INSERT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE POLICY tenant_update ON workflow_runs FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
CREATE POLICY tenant_update ON workflow_run_steps FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
GRANT UPDATE(revision,status,updated_at) ON workflow_runs TO app_login;
GRANT UPDATE(status,attempt,current_candidate_id,document_id,skip_reason) ON workflow_run_steps TO app_login;
CREATE FUNCTION workflow_revision_advance() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.revision<>OLD.revision+1 THEN
  RAISE EXCEPTION 'workflow revision must advance exactly once' USING ERRCODE='23514';
 END IF;
 NEW.updated_at:=clock_timestamp();RETURN NEW;
END $$;
CREATE TRIGGER workflow_revision_advance BEFORE UPDATE ON workflow_runs
 FOR EACH ROW EXECUTE FUNCTION workflow_revision_advance();
CREATE TRIGGER workflow_candidate_immutable BEFORE UPDATE OR DELETE ON workflow_step_candidates
 FOR EACH ROW EXECUTE FUNCTION workflow_template_immutable();
CREATE TRIGGER workflow_action_immutable BEFORE UPDATE OR DELETE ON workflow_step_actions
 FOR EACH ROW EXECUTE FUNCTION workflow_template_immutable();
