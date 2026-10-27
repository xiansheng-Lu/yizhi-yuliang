-- M0任务表只保存归属/标识；旧行不凭空填充完整申请。
ALTER TABLE command_jobs DROP CONSTRAINT command_jobs_status_check;
ALTER TABLE command_jobs ADD CONSTRAINT command_jobs_status_check CHECK(status IN (
 'DRAFT','PENDING_APPROVAL','APPROVED','EXECUTING','VERIFYING','SUCCEEDED','FAILED',
 'REJECTED','CANCELLED','EXPIRED','BLOCKED','MANUAL_REQUIRED'));
ALTER TABLE command_jobs ADD COLUMN document jsonb,
 ADD COLUMN target_type varchar(20),ADD COLUMN target_id uuid,ADD COLUMN campaign_id uuid,
 ADD COLUMN request_digest varchar(64),ADD COLUMN intent_hash varchar(64),
 ADD COLUMN idempotency_key varchar(64),ADD COLUMN version bigint NOT NULL DEFAULT 1,
 ADD COLUMN expires_at timestamptz,ADD COLUMN target_proof jsonb;
ALTER TABLE command_jobs ADD CONSTRAINT fk_job_campaign
 FOREIGN KEY(tenant_id,campaign_id,shop_id,account_id) REFERENCES campaigns(tenant_id,id,shop_id,account_id);
ALTER TABLE command_jobs ADD CONSTRAINT fk_job_live_shop
 FOREIGN KEY(tenant_id,live_session_id,shop_id) REFERENCES live_sessions(tenant_id,id,shop_id);
ALTER TABLE command_jobs ADD CONSTRAINT ck_job_complete CHECK(document IS NULL OR (
 jsonb_typeof(document)='object' AND target_type IN ('AD_PLAN','LIVE_SESSION') AND target_id IS NOT NULL
 AND request_digest ~ '^[0-9a-f]{64}$' AND intent_hash ~ '^[0-9a-f]{64}$'
 AND idempotency_key ~ '^[A-Za-z0-9_.:-]{1,64}$' AND version>0 AND expires_at>created_at
 AND document->>'schema_version'='1.0' AND document->>'mode'='API'
 AND document->>'status'=status AND document->>'version'=version::text
 AND document->>'job_id'=id::text AND document->>'tenant_id'=tenant_id::text
 AND document->>'shop_id'=shop_id::text AND document->>'target_type'=target_type
 AND document->>'target_id'=target_id::text
 AND document->>'idempotency_key'=idempotency_key
 AND document#>>'{risk_summary,source}'='SERVER'
 AND jsonb_array_length(document#>'{risk_summary,checks}')>0
 AND ((target_type='AD_PLAN' AND campaign_id=target_id AND account_id IS NOT NULL)
   OR (target_type='LIVE_SESSION' AND live_session_id=target_id AND campaign_id IS NULL))
 AND jsonb_typeof(target_proof)='object'
) IS TRUE);
CREATE UNIQUE INDEX ux_job_idempotency ON command_jobs(tenant_id,idempotency_key)
 WHERE idempotency_key IS NOT NULL;
CREATE INDEX ix_job_target ON command_jobs(tenant_id,target_type,target_id);
CREATE INDEX ix_job_state ON command_jobs(tenant_id,status,updated_at DESC);
GRANT INSERT ON command_jobs TO app_login;

CREATE TABLE job_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,tenant_id bigint NOT NULL,job_id uuid NOT NULL,
 from_status varchar(20),to_status varchar(20) NOT NULL,
 actor_id uuid NOT NULL REFERENCES users(id),request_id uuid NOT NULL,
 reason_code varchar(60) NOT NULL,detail varchar(300) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id)
);
ALTER TABLE job_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_events FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON job_events TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY job_private ON job_events TO app_login
 USING(tenant_id=public.current_tenant_id() AND EXISTS(
  SELECT 1 FROM command_jobs j WHERE j.tenant_id=job_events.tenant_id AND j.id=job_events.job_id))
 WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id() AND EXISTS(
  SELECT 1 FROM command_jobs j WHERE j.tenant_id=job_events.tenant_id AND j.id=job_events.job_id));
GRANT SELECT,INSERT ON job_events TO app_login;
GRANT USAGE ON SEQUENCE job_events_id_seq TO app_login;

-- 映射由有证明的镜像接入负责登记，用户不能将任意源/字段当当前计划值。
CREATE TABLE command_value_sources (
 tenant_id bigint NOT NULL,campaign_id uuid NOT NULL,shop_id uuid NOT NULL,account_id uuid NOT NULL,
 source_id uuid NOT NULL,authorization_id uuid NOT NULL,provider varchar(20) NOT NULL,
 product_line varchar(20) NOT NULL,action_type varchar(30) NOT NULL CHECK(action_type IN (
  'CHANGE_DAILY_BUDGET','CHANGE_BID','SET_ROI_TARGET','PAUSE_AD_PLAN','RESUME_AD_PLAN')),
 value_field varchar(40) NOT NULL CHECK(value_field ~ '^[a-z][a-z0-9_]{0,39}$'),
 proof_ref varchar(180) NOT NULL CHECK(proof_ref ~ '^[A-Za-z0-9_.:/-]{1,180}$'),
 PRIMARY KEY(tenant_id,campaign_id,action_type),
 FOREIGN KEY(tenant_id,campaign_id,shop_id,account_id) REFERENCES campaigns(tenant_id,id,shop_id,account_id),
 FOREIGN KEY(tenant_id,source_id,account_id) REFERENCES sync_sources(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,authorization_id,provider,product_line)
  REFERENCES provider_authorizations(tenant_id,id,provider,product_line)
);
ALTER TABLE command_value_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE command_value_sources FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON command_value_sources TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON command_value_sources FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id());
GRANT SELECT ON command_value_sources TO app_login;
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create'));
