CREATE FUNCTION access.ai_spec_valid(p jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
 SELECT coalesce(jsonb_typeof(p)='object'
  AND p ?& ARRAY['model_key','provider','model','endpoint_ref','secret_ref','billing_currency']
  AND p-ARRAY['model_key','provider','model','endpoint_ref','secret_ref','billing_currency']='{}'::jsonb
  AND p->>'billing_currency' IN ('CNY','USD')
  AND jsonb_typeof(p->'model_key')='string' AND jsonb_typeof(p->'provider')='string'
  AND jsonb_typeof(p->'model')='string' AND jsonb_typeof(p->'endpoint_ref')='string'
  AND jsonb_typeof(p->'secret_ref')='string'
  AND p->>'model_key' ~ '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,79}$'
  AND p->>'provider' ~ '^[a-z][a-z0-9_-]{0,29}$'
  AND p->>'model' ~ '^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}$'
  AND p->>'endpoint_ref' ~ '^[A-Z][A-Z0-9_]{2,79}$'
  AND p->>'secret_ref' ~ '^[A-Z][A-Z0-9_]{2,79}$',false);
$$;
REVOKE ALL ON FUNCTION access.ai_spec_valid(jsonb) FROM PUBLIC,app_login,app_auth;
CREATE TABLE model_configs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id bigint NOT NULL REFERENCES tenants(id),
 capability_alias varchar(80) NOT NULL CHECK(capability_alias ~ '^[a-z][a-z0-9_.-]{0,79}$'),
 revision integer NOT NULL CHECK(revision>0),
 primary_spec jsonb NOT NULL CHECK(access.ai_spec_valid(primary_spec)),
 backup_spec jsonb CHECK(backup_spec IS NULL OR access.ai_spec_valid(backup_spec)),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,capability_alias,revision)
);
CREATE TABLE prompt_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id bigint NOT NULL REFERENCES tenants(id),
 prompt_key varchar(80) NOT NULL CHECK(prompt_key ~ '^[a-z][a-z0-9_.-]{0,79}$'),
 version integer NOT NULL CHECK(version>0),
 capability_alias varchar(80) NOT NULL,
 template text NOT NULL CHECK(length(template) BETWEEN 1 AND 16000),
 content_hash varchar(64) NOT NULL CHECK(content_hash=encode(sha256(convert_to(template,'UTF8')),'hex')),
 output_schema_ref varchar(120) NOT NULL CHECK(output_schema_ref ~ '^[a-zA-Z][a-zA-Z0-9._/-]{0,119}$'),
 status varchar(10) NOT NULL CHECK(status IN ('DRAFT','ACTIVE','DISABLED')),
 created_by uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,prompt_key,version)
);
CREATE TABLE ai_invocations (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 call_id uuid NOT NULL,
 tenant_id bigint NOT NULL REFERENCES tenants(id),
 user_id uuid NOT NULL REFERENCES users(id), membership_id uuid NOT NULL,
 request_id uuid NOT NULL,
 scenario varchar(40) NOT NULL CHECK(scenario IN ('command_parse','decision_diagnose','report_narrate','copywriting')),
 model_config_id uuid NOT NULL, prompt_id uuid NOT NULL,
 model_role varchar(8) NOT NULL CHECK(model_role IN ('PRIMARY','BACKUP')),
 parent_call_id uuid,
 model_key varchar(80) NOT NULL,
 prompt_version integer NOT NULL,
 model_config_snapshot jsonb NOT NULL,
 status varchar(12) NOT NULL DEFAULT 'STARTED'
  CHECK(status IN ('STARTED','OK','FAILED','TIMEOUT','RATE_LIMITED','FALLBACK')),
 dispatch_state varchar(10) NOT NULL DEFAULT 'NOT_SENT'
  CHECK(dispatch_state IN ('NOT_SENT','UNKNOWN','RESPONDED')),
 usage_state varchar(7) NOT NULL DEFAULT 'UNKNOWN' CHECK(usage_state IN ('UNKNOWN','KNOWN')),
 cost_state varchar(7) NOT NULL DEFAULT 'UNKNOWN' CHECK(cost_state IN ('UNKNOWN','KNOWN')),
 billing_currency varchar(3) NOT NULL CHECK(billing_currency IN ('CNY','USD')),
 cost_currency varchar(3),
 tokens_in bigint CHECK(tokens_in>=0),tokens_out bigint CHECK(tokens_out>=0),
 cost_minor bigint CHECK(cost_minor>=0),latency_ms integer CHECK(latency_ms>=0),
 external_request_id varchar(128),
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(tenant_id,call_id),
 FOREIGN KEY(tenant_id,membership_id,user_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,model_config_id) REFERENCES model_configs(tenant_id,id),
 FOREIGN KEY(tenant_id,prompt_id) REFERENCES prompt_versions(tenant_id,id),
 FOREIGN KEY(tenant_id,parent_call_id) REFERENCES ai_invocations(tenant_id,call_id),
 CHECK((model_role='PRIMARY' AND parent_call_id IS NULL) OR
       (model_role='BACKUP' AND parent_call_id IS NOT NULL AND parent_call_id<>call_id)),
 CHECK((usage_state='UNKNOWN' AND tokens_in IS NULL AND tokens_out IS NULL) OR
       (usage_state='KNOWN' AND tokens_in IS NOT NULL AND tokens_out IS NOT NULL)),
 CHECK((cost_state='UNKNOWN' AND cost_minor IS NULL AND cost_currency IS NULL) OR
       (cost_state='KNOWN' AND cost_minor IS NOT NULL AND cost_currency IS NOT NULL AND cost_currency=billing_currency)),
 CHECK(status<>'OK' OR dispatch_state='RESPONDED'),
 CHECK((status='STARTED' AND finished_at IS NULL AND latency_ms IS NULL
        AND usage_state='UNKNOWN' AND cost_state='UNKNOWN') OR
       (status<>'STARTED' AND finished_at IS NOT NULL AND latency_ms IS NOT NULL))
);
CREATE INDEX ix_ai_invocation_scope ON ai_invocations(tenant_id,scenario,created_at DESC);
DO $$ DECLARE target text; BEGIN
 FOREACH target IN ARRAY ARRAY['model_configs','prompt_versions','ai_invocations'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',target);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',target);
  EXECUTE format('CREATE POLICY ai_owner ON public.%I TO app_owner USING(true) WITH CHECK(true)',target);
  EXECUTE format('CREATE POLICY ai_select ON public.%I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',target);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,app_login,app_auth',target);
  EXECUTE format('GRANT SELECT ON public.%I TO app_login',target);
 END LOOP;
END; $$;

CREATE FUNCTION access.ai_begin(p_call uuid,p_request uuid,p_member uuid,p_user uuid,p_scenario text,
 p_alias text,p_prompt text,p_version integer,p_role text,p_parent uuid)
RETURNS TABLE(invocation_id bigint,model_snapshot jsonb,prompt_hash varchar)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE config public.model_configs%ROWTYPE; prompt public.prompt_versions%ROWTYPE;
 selected jsonb; created bigint; snapshot jsonb; parent public.ai_invocations%ROWTYPE;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.memberships m JOIN public.tenants t ON t.id=m.tenant_id
   JOIN public.users u ON u.id=m.user_id WHERE m.id=p_member AND m.user_id=p_user
   AND m.tenant_id=public.current_tenant_id() AND m.status='ACTIVE'
   AND t.status='ACTIVE' AND t.deleted_at IS NULL AND u.status='ACTIVE' AND u.deleted_at IS NULL)
 THEN RAISE EXCEPTION 'AI scope denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO prompt FROM public.prompt_versions WHERE tenant_id=public.current_tenant_id()
  AND prompt_key=p_prompt AND version=p_version AND capability_alias=p_alias AND status='ACTIVE';
 IF NOT FOUND THEN RAISE EXCEPTION 'prompt unavailable' USING ERRCODE='22023'; END IF;
 IF p_role='PRIMARY' AND p_parent IS NULL THEN
  SELECT * INTO config FROM public.model_configs WHERE tenant_id=public.current_tenant_id()
   AND capability_alias=p_alias ORDER BY revision DESC LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION 'route unavailable' USING ERRCODE='22023'; END IF;
  selected:=config.primary_spec;
 ELSIF p_role='BACKUP' AND p_parent IS NOT NULL THEN
  SELECT * INTO parent FROM public.ai_invocations WHERE tenant_id=public.current_tenant_id()
   AND call_id=p_parent AND user_id=p_user AND request_id=p_request AND scenario=p_scenario
   AND prompt_id=prompt.id AND model_role='PRIMARY'
   AND status IN ('FAILED','TIMEOUT','RATE_LIMITED');
  IF NOT FOUND THEN RAISE EXCEPTION 'backup source unavailable' USING ERRCODE='22023'; END IF;
  SELECT * INTO config FROM public.model_configs WHERE tenant_id=parent.tenant_id AND id=parent.model_config_id;
  selected:=config.backup_spec;
 ELSE RAISE EXCEPTION 'invalid model role' USING ERRCODE='22023'; END IF;
 IF selected IS NULL THEN RAISE EXCEPTION 'model unavailable' USING ERRCODE='22023'; END IF;
 snapshot:=jsonb_build_object('config_id',config.id,'revision',config.revision,
  'primary',config.primary_spec,'backup',config.backup_spec,'selected',selected,
  'prompt_hash',prompt.content_hash,'output_schema_ref',prompt.output_schema_ref);
 INSERT INTO public.ai_invocations(call_id,tenant_id,user_id,membership_id,request_id,scenario,
   model_config_id,prompt_id,model_role,parent_call_id,model_key,prompt_version,model_config_snapshot,billing_currency)
 VALUES(p_call,public.current_tenant_id(),p_user,p_member,p_request,p_scenario,config.id,prompt.id,
   p_role,p_parent,selected->>'model_key',prompt.version,snapshot,selected->>'billing_currency') RETURNING id INTO created;
 RETURN QUERY SELECT created,snapshot,prompt.content_hash;
END; $$;
CREATE FUNCTION access.ai_finish(p_call uuid,p_user uuid,p_status text,p_usage text,p_cost text,
 p_input bigint,p_output bigint,p_amount bigint,p_latency integer,p_external text,p_dispatch text,p_currency text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF p_status NOT IN ('OK','FAILED','TIMEOUT','RATE_LIMITED','FALLBACK') THEN
  RAISE EXCEPTION 'invalid completion' USING ERRCODE='22023'; END IF;
 UPDATE public.ai_invocations SET status=p_status,usage_state=p_usage,cost_state=p_cost,
  tokens_in=p_input,tokens_out=p_output,cost_minor=p_amount,latency_ms=p_latency,
  external_request_id=p_external,dispatch_state=p_dispatch,cost_currency=p_currency,finished_at=statement_timestamp()
 WHERE call_id=p_call AND tenant_id=public.current_tenant_id() AND user_id=p_user AND status='STARTED';
 RETURN FOUND;
END; $$;
REVOKE ALL ON FUNCTION access.ai_begin(uuid,uuid,uuid,uuid,text,text,text,integer,text,uuid),
 access.ai_finish(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.ai_begin(uuid,uuid,uuid,uuid,text,text,text,integer,text,uuid),
 access.ai_finish(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text) TO app_login;
