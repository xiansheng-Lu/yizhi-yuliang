ALTER TABLE ai_invocations ADD CONSTRAINT ux_ai_invocations_actor_call
 UNIQUE(tenant_id,user_id,call_id);
CREATE TABLE interpretations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id bigint NOT NULL REFERENCES tenants(id), user_id uuid NOT NULL,
 request_id uuid NOT NULL, input_hash varchar(64) NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 scope jsonb NOT NULL CHECK(jsonb_typeof(scope)='object'),
 result jsonb NOT NULL CHECK(jsonb_typeof(result)='object' AND
   coalesce(result->>'kind' IN ('QUERY','ACTION','UNRECOGNIZED'),false)),
 source varchar(20) NOT NULL CHECK(source IN ('RULES','MODEL_GUARDED','MODEL_UNAVAILABLE')),
 rules_version varchar(40) NOT NULL, prompt_version integer NOT NULL CHECK(prompt_version>0),
 prompt_hash varchar(64) NOT NULL CHECK(prompt_hash ~ '^[0-9a-f]{64}$'), call_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,user_id,call_id) REFERENCES ai_invocations(tenant_id,user_id,call_id),
 CHECK((source='RULES' AND call_id IS NULL) OR (source<>'RULES' AND call_id IS NOT NULL))
);
ALTER TABLE interpretations ENABLE ROW LEVEL SECURITY;
ALTER TABLE interpretations FORCE ROW LEVEL SECURITY;
CREATE POLICY interpretations_owner ON interpretations TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY interpretations_private ON interpretations TO app_login
 USING(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id())
 WITH CHECK(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id());
REVOKE ALL ON interpretations FROM PUBLIC,app_login,app_auth;
GRANT SELECT,INSERT ON interpretations TO app_login;
CREATE INDEX ix_interpretations_request ON interpretations(tenant_id,user_id,request_id);
