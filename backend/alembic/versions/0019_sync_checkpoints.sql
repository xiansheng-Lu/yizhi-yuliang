ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK(object_type IN (
 'user','shop','member','role','project','conversation','message','job','platform_request',
 'oauth_state','authorization','sync_source'));
ALTER TABLE platform_requests ADD CONSTRAINT ux_platform_request_tenant UNIQUE(tenant_id,id);
ALTER TABLE capability_bindings ADD CONSTRAINT ux_binding_account UNIQUE(tenant_id,id,account_id);
ALTER TABLE platform_requests ADD CONSTRAINT fk_request_binding_account
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id);
CREATE TABLE sync_sources (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL REFERENCES tenants(id),account_id uuid NOT NULL,
 binding_id uuid NOT NULL,resource_kind varchar(60) NOT NULL,
 context_digest varchar(64) NOT NULL,filter_digest varchar(64) NOT NULL,enabled boolean NOT NULL DEFAULT true,
 cursor_enc bytea,cursor_key_version integer,cursor_digest varchar(64) NOT NULL,
 version bigint NOT NULL DEFAULT 0 CHECK(version>=0),active_run_id uuid,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,account_id,binding_id,resource_kind,filter_digest),
 FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 CHECK((cursor_enc IS NULL)=(cursor_key_version IS NULL))
);
CREATE TABLE sync_runs (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,source_id uuid NOT NULL,user_id uuid NOT NULL REFERENCES users(id),
 request_digest varchar(64) NOT NULL,source_version bigint NOT NULL,
 state varchar(15) NOT NULL CHECK(state IN ('RUNNING','SUCCESS','PARTIAL','FAILED','RATE_LIMITED','PAUSED')),
 page_no integer NOT NULL DEFAULT 0 CHECK(page_no>=0),items_count bigint NOT NULL DEFAULT 0 CHECK(items_count>=0),
 lease_token uuid NOT NULL,lease_until timestamptz NOT NULL,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),finished_at timestamptz,last_error varchar(40),
 UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,source_id) REFERENCES sync_sources(tenant_id,id)
);
CREATE TABLE sync_page_intents (
 outbound_id uuid PRIMARY KEY,tenant_id bigint NOT NULL,run_id uuid NOT NULL,page_no integer NOT NULL,
 lease_token uuid NOT NULL,input_cursor_digest varchar(64) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(run_id,page_no,lease_token),UNIQUE(tenant_id,outbound_id),
 FOREIGN KEY(tenant_id,run_id) REFERENCES sync_runs(tenant_id,id)
);
CREATE TABLE sync_pages (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,tenant_id bigint NOT NULL,run_id uuid NOT NULL,
 page_no integer NOT NULL,outbound_id uuid NOT NULL UNIQUE,input_cursor_digest varchar(64) NOT NULL,
 output_cursor_digest varchar(64) NOT NULL,page_digest varchar(64) NOT NULL,terminal boolean NOT NULL,
 records_count integer NOT NULL,committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(run_id,page_no),
 FOREIGN KEY(tenant_id,run_id) REFERENCES sync_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,outbound_id) REFERENCES sync_page_intents(tenant_id,outbound_id),
 FOREIGN KEY(tenant_id,outbound_id) REFERENCES platform_requests(tenant_id,id)
);
CREATE TABLE sync_resource_mirrors (
 tenant_id bigint NOT NULL,source_id uuid NOT NULL,native_id varchar(128) NOT NULL,
 projection jsonb NOT NULL,outbound_id uuid NOT NULL,source_at timestamptz,
 fetched_at timestamptz NOT NULL,updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,source_id,native_id),
 FOREIGN KEY(tenant_id,source_id) REFERENCES sync_sources(tenant_id,id),
 FOREIGN KEY(tenant_id,outbound_id) REFERENCES platform_requests(tenant_id,id)
);
CREATE INDEX ix_sync_runs_health ON sync_runs(tenant_id,source_id,started_at DESC);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['sync_sources','sync_runs','sync_page_intents','sync_pages','sync_resource_mirrors'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I FOR ALL TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT,INSERT ON %I TO app_login',item);
 END LOOP;
 FOREACH item IN ARRAY ARRAY['sync_sources','sync_runs','sync_resource_mirrors'] LOOP
  EXECUTE format('CREATE POLICY tenant_update ON %I FOR UPDATE TO app_login USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id())',item);
 END LOOP;
END $$;
GRANT UPDATE(enabled,cursor_enc,cursor_key_version,cursor_digest,version,active_run_id) ON sync_sources TO app_login;
GRANT UPDATE(source_version,state,page_no,items_count,lease_token,lease_until,finished_at,last_error) ON sync_runs TO app_login;
GRANT UPDATE(projection,outbound_id,source_at,fetched_at,updated_at) ON sync_resource_mirrors TO app_login;
GRANT USAGE ON SEQUENCE sync_pages_id_seq TO app_login;
