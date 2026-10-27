CREATE TABLE risk_policies (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL UNIQUE REFERENCES tenants(id),
 version bigint NOT NULL CHECK(version>0),UNIQUE(tenant_id,id)
);
CREATE TABLE policy_versions (
 tenant_id bigint NOT NULL,policy_id uuid NOT NULL,version bigint NOT NULL CHECK(version>0),
 config jsonb NOT NULL CHECK(jsonb_typeof(config)='object'),
 proof_ref varchar(180) NOT NULL CHECK(proof_ref ~ '^[A-Za-z0-9_.:/-]{1,180}$'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,policy_id,version),
 FOREIGN KEY(tenant_id,policy_id) REFERENCES risk_policies(tenant_id,id) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE risk_policies ADD CONSTRAINT fk_current_policy_version
 FOREIGN KEY(tenant_id,id,version) REFERENCES policy_versions(tenant_id,policy_id,version)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE risk_assessments (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,job_id uuid NOT NULL,
 user_id uuid NOT NULL,membership_id uuid NOT NULL,request_id uuid NOT NULL,
 stage varchar(10) NOT NULL CHECK(stage IN ('CREATE','CONFIRM','APPROVE','EXECUTE')),
 job_version bigint CHECK(job_version>0),policy_id uuid,policy_version bigint NOT NULL,
 checked_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 summary jsonb NOT NULL,evidence jsonb NOT NULL,
 CHECK(expires_at>checked_at AND expires_at<=checked_at+interval '30 seconds'),
 CHECK((policy_id IS NULL AND policy_version=0) OR (policy_id IS NOT NULL AND policy_version>0)),
 CHECK((jsonb_typeof(summary)='object' AND summary->>'source'='SERVER'
  AND jsonb_array_length(summary->'checks')>0 AND jsonb_typeof(evidence)='array') IS TRUE),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(tenant_id,membership_id,user_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,policy_id,policy_version) REFERENCES policy_versions(tenant_id,policy_id,version)
);
CREATE INDEX ix_risk_assessment_job ON risk_assessments(tenant_id,job_id,checked_at DESC,id);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['risk_policies','policy_versions','risk_assessments'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
 END LOOP;
END $$;
CREATE POLICY tenant_read ON risk_policies FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
CREATE POLICY tenant_read ON policy_versions FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
GRANT SELECT ON risk_policies,policy_versions TO app_login;
CREATE POLICY private_actor ON risk_assessments TO app_login
 USING(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id())
 WITH CHECK(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id());
GRANT SELECT,INSERT ON risk_assessments TO app_login;
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition','risk.assess'));
