ALTER TABLE ai_invocations ADD CONSTRAINT ai_content_call_identity_key
 UNIQUE(tenant_id,call_id,prompt_id,model_config_id);
ALTER TABLE workflow_step_actions DROP CONSTRAINT workflow_step_actions_action_check;
ALTER TABLE workflow_step_actions ADD CONSTRAINT workflow_step_actions_action_check
 CHECK(action IN ('execute','accept','skip','redo','model_begin','model_complete','model_failure'));
ALTER TABLE workflow_step_candidates ADD COLUMN producer text NOT NULL DEFAULT 'MANUAL' CHECK(producer IN ('MANUAL','MODEL'));
ALTER TABLE workflow_step_candidates ADD COLUMN call_id uuid;
ALTER TABLE workflow_step_candidates ADD COLUMN prompt_id uuid;
ALTER TABLE workflow_step_candidates ADD COLUMN model_config_id uuid;
ALTER TABLE workflow_step_candidates ADD CONSTRAINT content_candidate_model_source_fk
 FOREIGN KEY(tenant_id,call_id,prompt_id,model_config_id) REFERENCES ai_invocations(tenant_id,call_id,prompt_id,model_config_id);
ALTER TABLE workflow_step_candidates ADD CONSTRAINT content_candidate_model_source_check CHECK(
 (producer='MANUAL' AND call_id IS NULL AND prompt_id IS NULL AND model_config_id IS NULL) OR
 (producer='MODEL' AND call_id IS NOT NULL AND prompt_id IS NOT NULL AND model_config_id IS NOT NULL));
CREATE TABLE content_ai_requests (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,shop_id uuid NOT NULL,product_id uuid NOT NULL,
 run_id uuid NOT NULL,step_code varchar(40) NOT NULL,attempt integer NOT NULL CHECK(attempt>0),
 actor_id uuid NOT NULL REFERENCES users(id),request_key varchar(160) NOT NULL,request_hash varchar(64) NOT NULL,
 input_document_id uuid NOT NULL,input_revision integer NOT NULL,
 ai_scenario text NOT NULL CHECK(ai_scenario IN ('content_planning','content_script','content_storyboard')),
 context_hash varchar(64) NOT NULL,context jsonb NOT NULL,asset_snapshots jsonb NOT NULL,prepared jsonb NOT NULL,
 call_id uuid NOT NULL,prompt_id uuid NOT NULL,model_config_id uuid NOT NULL,
 quote_id uuid NOT NULL,permit_id uuid,candidate_id uuid,begin_revision integer,
 state text NOT NULL CHECK(state IN ('PREPARED','IN_FLIGHT','CANDIDATE','FAILED','STALE','UNKNOWN')),
 reason text,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,run_id,request_key),UNIQUE(tenant_id,call_id),
 FOREIGN KEY(tenant_id,run_id,step_code) REFERENCES workflow_run_steps(tenant_id,run_id,step_code),
 FOREIGN KEY(tenant_id,run_id,shop_id,product_id) REFERENCES workflow_runs(tenant_id,id,shop_id,product_id),
 FOREIGN KEY(tenant_id,input_document_id,input_revision) REFERENCES content_document_versions(tenant_id,document_id,revision),
 FOREIGN KEY(tenant_id,call_id,prompt_id,model_config_id) REFERENCES ai_invocations(tenant_id,call_id,prompt_id,model_config_id),
 FOREIGN KEY(tenant_id,quote_id) REFERENCES ai_cost_quotes(tenant_id,id),
 FOREIGN KEY(tenant_id,permit_id) REFERENCES ai_cost_permits(tenant_id,id),
 FOREIGN KEY(tenant_id,candidate_id) REFERENCES workflow_step_candidates(tenant_id,id)
);
ALTER TABLE content_ai_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_ai_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON content_ai_requests TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON content_ai_requests FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
CREATE POLICY tenant_insert ON content_ai_requests FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
CREATE POLICY tenant_update ON content_ai_requests FOR UPDATE TO app_login USING(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id()) WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
GRANT SELECT,INSERT ON content_ai_requests TO app_login;
GRANT UPDATE(permit_id,candidate_id,begin_revision,state,reason,updated_at) ON content_ai_requests TO app_login;

CREATE FUNCTION access.content_ai_failure(p_request uuid,p_reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE req public.content_ai_requests%ROWTYPE; run public.workflow_runs%ROWTYPE;
 st public.workflow_run_steps%ROWTYPE; terminal text; step_view jsonb; history_view jsonb;
 result_view jsonb; next_revision integer;
BEGIN
 IF p_reason IS NULL OR p_reason NOT IN ('STALE_REPLY','SCOPE_CHANGED','UPSTREAM_ERROR','TIMEOUT','RATE_LIMITED','INVALID_RESPONSE','RESULT_UNKNOWN')
 THEN RAISE EXCEPTION 'invalid content failure reason' USING ERRCODE='22023'; END IF;
 SELECT * INTO req FROM public.content_ai_requests WHERE tenant_id=public.current_tenant_id()
  AND id=p_request AND actor_id=public.current_actor_id();
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO run FROM public.workflow_runs WHERE tenant_id=req.tenant_id AND id=req.run_id FOR UPDATE;
 SELECT * INTO req FROM public.content_ai_requests WHERE tenant_id=req.tenant_id AND id=p_request FOR UPDATE;
 IF req.state<>'IN_FLIGHT' THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.ai_invocations a JOIN public.ai_cost_permits p
  ON (p.tenant_id,p.id,p.request_key)=(a.tenant_id,req.permit_id,a.call_id)
  WHERE (a.tenant_id,a.call_id,a.prompt_id,a.model_config_id,a.user_id)=
   (req.tenant_id,req.call_id,req.prompt_id,req.model_config_id,req.actor_id)
  AND a.scenario='copywriting' AND p.state IN ('IN_FLIGHT','UNKNOWN','SETTLED'))
 THEN RAISE EXCEPTION 'missing content invocation permit' USING ERRCODE='42501'; END IF;
 terminal:=CASE WHEN p_reason IN ('STALE_REPLY','SCOPE_CHANGED') THEN 'STALE'
  WHEN p_reason='RESULT_UNKNOWN' THEN 'UNKNOWN' ELSE 'FAILED' END;
 SELECT * INTO st FROM public.workflow_run_steps WHERE tenant_id=req.tenant_id
  AND run_id=req.run_id AND step_code=req.step_code;
 UPDATE public.content_ai_requests SET state=terminal,reason=p_reason,updated_at=clock_timestamp() WHERE id=req.id;
 UPDATE public.content_asset_references SET active=false WHERE tenant_id=req.tenant_id
  AND reference_id=req.id AND kind='SOP' AND active;
 IF run.status<>'RUNNING' OR st.attempt<>req.attempt OR st.status<>'RUNNING' THEN RETURN false; END IF;
 IF (SELECT count(*) FROM public.workflow_step_actions WHERE tenant_id=req.tenant_id AND run_id=req.run_id)>=1000
 THEN RAISE EXCEPTION 'content action quota invariant' USING ERRCODE='54000'; END IF;
 UPDATE public.workflow_run_steps SET status='FAILED' WHERE tenant_id=req.tenant_id AND run_id=req.run_id AND step_code=req.step_code;
 UPDATE public.workflow_runs SET revision=revision+1 WHERE tenant_id=req.tenant_id AND id=req.run_id RETURNING revision INTO next_revision;
 SELECT coalesce(jsonb_agg(jsonb_build_object('code',step_code,'status',status,'attempt',attempt,
  'candidateId',current_candidate_id::text,'documentId',document_id::text,'skipReason',skip_reason) ORDER BY seq),'[]')
 INTO step_view FROM public.workflow_run_steps WHERE tenant_id=req.tenant_id AND run_id=req.run_id;
 SELECT coalesce(jsonb_agg(jsonb_build_object('actionKey',action_key,'code',step_code,'action',action,'reason',reason,
  'revision',result->'revision','outputs',result->'steps') ORDER BY (result->>'revision')::integer,action_key),'[]')
 INTO history_view FROM public.workflow_step_actions WHERE tenant_id=req.tenant_id AND run_id=req.run_id;
 history_view:=history_view||jsonb_build_array(jsonb_build_object('actionKey','model:'||req.id||':failure',
  'code',req.step_code,'action','model_failure','reason',p_reason,'revision',next_revision,'outputs',step_view));
 result_view:=jsonb_build_object('id',run.id::text,'revision',next_revision,'status',run.status,
  'rootDocumentId',run.root_document_id::text,'steps',step_view,'history',history_view,'candidates','[]'::jsonb);
 INSERT INTO public.workflow_step_actions(tenant_id,run_id,action_key,step_code,action,request_hash,result,reason,created_by)
 VALUES(req.tenant_id,req.run_id,'model:'||req.id||':failure',req.step_code,'model_failure',
  encode(sha256(convert_to(req.id::text||':'||p_reason,'UTF8')),'hex'),result_view,p_reason,req.actor_id);
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION access.content_ai_failure(uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.content_ai_failure(uuid,text) TO app_login;

CREATE FUNCTION access.workflow_action_occupancy(p_run uuid) RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE number bigint;
BEGIN
 IF public.current_tenant_id() IS NULL OR NOT EXISTS(SELECT 1 FROM public.workflow_runs
  WHERE tenant_id=public.current_tenant_id() AND id=p_run) OR NOT access.workflow_stream_visible(p_run)
 THEN RAISE EXCEPTION 'workflow action occupancy denied' USING ERRCODE='42501'; END IF;
 SELECT (SELECT count(*) FROM public.workflow_step_actions WHERE tenant_id=public.current_tenant_id() AND run_id=p_run)
  +(SELECT count(*) FROM public.content_ai_requests WHERE tenant_id=public.current_tenant_id() AND run_id=p_run AND state='IN_FLIGHT')
 INTO number;
 RETURN number;
END $$;
REVOKE ALL ON FUNCTION access.workflow_action_occupancy(uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.workflow_action_occupancy(uuid) TO app_login;
