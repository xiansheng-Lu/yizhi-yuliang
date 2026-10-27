ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project','platform.request'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check
 CHECK(object_type IN ('user','shop','member','role','project','conversation','message','job','platform_request'));
CREATE TABLE platform_requests (
 id uuid PRIMARY KEY, tenant_id bigint NOT NULL REFERENCES tenants(id),user_id uuid NOT NULL,
 membership_id uuid NOT NULL,request_id uuid NOT NULL,authorization_id uuid NOT NULL,
 account_id uuid NOT NULL,binding_id uuid NOT NULL,capability_version integer NOT NULL CHECK(capability_version>0),
 connector_id varchar(20) NOT NULL,provider varchar(20) NOT NULL,product_line varchar(30) NOT NULL,
 catalog_sha256 varchar(64) NOT NULL,catalog_id varchar(20) NOT NULL,
 operation_key varchar(80) NOT NULL,effect varchar(16) NOT NULL CHECK(effect IN ('READ','WRITE','TOKEN_REFRESH')),
 method varchar(8) NOT NULL,normalized_path text NOT NULL,
 target_native_id varchar(128),execution_id uuid,execution_attempt_id uuid,
 request_digest varchar(64) NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
 request_json jsonb NOT NULL CHECK(jsonb_typeof(request_json)='object'),audit_id bigint NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,membership_id,user_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 FOREIGN KEY(tenant_id,binding_id) REFERENCES capability_bindings(tenant_id,id),
 FOREIGN KEY(catalog_sha256,catalog_id) REFERENCES endpoint_catalog(catalog_sha256,catalog_id),
 CHECK((execution_id IS NULL)=(execution_attempt_id IS NULL))
);
CREATE INDEX ix_platform_request_trace ON platform_requests(tenant_id,request_id,created_at);
CREATE TABLE platform_raw_payloads (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,tenant_id bigint NOT NULL,
 user_id uuid NOT NULL,outbound_id uuid NOT NULL,network_attempt integer NOT NULL CHECK(network_attempt BETWEEN 0 AND 3),
 phase varchar(10) NOT NULL CHECK(phase IN ('ATTEMPT','RESPONSE','ERROR')),
 dispatch varchar(12) NOT NULL CHECK(dispatch IN ('NOT_SENT','SENDING','RESPONDED','UNKNOWN')),
 http_status integer CHECK(http_status BETWEEN 100 AND 599),category varchar(40),external_outcome varchar(20)
 CHECK(external_outcome IN ('ACCEPTED','REJECTED','UNKNOWN','NOT_SENT','READ_SUCCEEDED','READ_FAILED')),
 retry_after_seconds integer CHECK(retry_after_seconds BETWEEN 0 AND 86400),
 retry_after_unconfirmed boolean NOT NULL DEFAULT false,
 response_digest varchar(64) CHECK(response_digest ~ '^[0-9a-f]{64}$'),
 trace_digest varchar(64) CHECK(trace_digest ~ '^[0-9a-f]{64}$'),
 payload jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(payload)='object'),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,outbound_id,user_id) REFERENCES platform_requests(tenant_id,id,user_id),
 UNIQUE(tenant_id,outbound_id,network_attempt,phase),
 CHECK((phase='ATTEMPT' AND dispatch='SENDING' AND network_attempt>0 AND category IS NULL AND external_outcome IS NULL)
  OR (phase<>'ATTEMPT' AND dispatch<>'SENDING' AND category IS NOT NULL AND external_outcome IS NOT NULL))
);
CREATE UNIQUE INDEX ux_platform_attempt_outcome ON platform_raw_payloads(tenant_id,outbound_id,network_attempt)
 WHERE phase IN ('RESPONSE','ERROR');
CREATE INDEX ix_platform_trace_digest ON platform_raw_payloads(tenant_id,trace_digest) WHERE trace_digest IS NOT NULL;
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['platform_requests','platform_raw_payloads'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY payload_maintenance ON public.%I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY payload_private ON public.%I TO app_login USING(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id()) WITH CHECK(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id())',item);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,app_login,app_auth',item);
  EXECUTE format('GRANT SELECT,INSERT ON public.%I TO app_login',item);
 END LOOP;
END $$;
GRANT USAGE ON SEQUENCE platform_raw_payloads_id_seq TO app_login;
