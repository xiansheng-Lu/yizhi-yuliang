ALTER TABLE risk_assessments ADD CONSTRAINT ux_risk_tenant_id UNIQUE(tenant_id,id);
CREATE TABLE server_approval_permits (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,policy_id uuid NOT NULL,policy_version bigint NOT NULL,
 policy_actor_membership_id uuid NOT NULL,account_id uuid NOT NULL,campaign_id uuid NOT NULL,
 action varchar(30) NOT NULL CHECK(action IN ('CHANGE_DAILY_BUDGET','CHANGE_BID','PAUSE_AD_PLAN')),
 max_change_minor bigint CHECK(max_change_minor>0 AND max_change_minor<=9007199254740991),
 max_change_percent numeric CHECK(max_change_percent>0 AND max_change_percent<=100),
 important boolean,classification_ref varchar(180),valid_from timestamptz NOT NULL,
 valid_until timestamptz NOT NULL,proof_ref varchar(180) NOT NULL,active boolean NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,policy_id,policy_version) REFERENCES policy_versions(tenant_id,policy_id,version),
 FOREIGN KEY(tenant_id,policy_actor_membership_id) REFERENCES memberships(tenant_id,id),
 FOREIGN KEY(tenant_id,campaign_id,account_id) REFERENCES campaigns(tenant_id,id,account_id),
 CHECK(valid_until>valid_from),CHECK(proof_ref ~ '^[A-Za-z0-9_.:/-]{1,180}$'),
 CHECK((action='PAUSE_AD_PLAN' AND important IS FALSE AND classification_ref IS NOT NULL
   AND classification_ref ~ '^[A-Za-z0-9_.:/-]{1,180}$')
  OR (action<>'PAUSE_AD_PLAN' AND max_change_minor IS NOT NULL AND max_change_percent IS NOT NULL))
);
CREATE TABLE approvals (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,biz_type varchar(30) NOT NULL,biz_id uuid NOT NULL,
 command_job_id uuid,requester_id uuid NOT NULL,requester_membership_id uuid NOT NULL,
 resource_version bigint NOT NULL CHECK(resource_version>0),version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 policy_id uuid NOT NULL,policy_version bigint NOT NULL,intent_hash varchar(64) NOT NULL,
 payload jsonb NOT NULL,payload_digest varchar(64) NOT NULL,
 state varchar(10) NOT NULL CHECK(state IN ('PENDING','APPROVED','REJECTED')),
 decision_id uuid,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,biz_type,biz_id,resource_version),UNIQUE(tenant_id,command_job_id),
 UNIQUE(tenant_id,id,command_job_id),
 FOREIGN KEY(tenant_id,command_job_id) REFERENCES command_jobs(tenant_id,id),
 FOREIGN KEY(tenant_id,requester_membership_id,requester_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,policy_id,policy_version) REFERENCES policy_versions(tenant_id,policy_id,version),
 CHECK(((biz_type='COMMAND_JOB' AND command_job_id=biz_id) OR (biz_type IN
  ('PLAN_CHANGE','CONFIG_TASK','BUDGET_REQUEST','PLATFORM_COMMAND') AND command_job_id IS NULL)) IS TRUE),
 CHECK(intent_hash ~ '^[0-9a-f]{64}$' AND payload_digest ~ '^[0-9a-f]{64}$'),
 CHECK(jsonb_typeof(payload)='object'),CHECK((state='PENDING')=(decision_id IS NULL))
);
CREATE TABLE approval_decisions (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,approval_id uuid NOT NULL,command_job_id uuid,
 decision varchar(7) NOT NULL CHECK(decision IN ('APPROVE','REJECT')),
 method varchar(15) NOT NULL CHECK(method IN ('MANUAL','SERVER_POLICY')),
 actor_kind varchar(8) NOT NULL CHECK(actor_kind IN ('HUMAN','POLICY')),
 actor_id uuid NOT NULL,membership_id uuid NOT NULL,request_id uuid NOT NULL,
 approval_version_before bigint NOT NULL,approval_version_after bigint NOT NULL,
 resource_version_before bigint NOT NULL,resource_version_after bigint NOT NULL,
 intent_hash varchar(64) NOT NULL,policy_id uuid NOT NULL,policy_version bigint NOT NULL,
 risk_assessment_id uuid,permit_id uuid,comment varchar(500),
 decided_at timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,approval_id),
 FOREIGN KEY(tenant_id,approval_id) REFERENCES approvals(tenant_id,id),
 FOREIGN KEY(tenant_id,approval_id,command_job_id) REFERENCES approvals(tenant_id,id,command_job_id),
 FOREIGN KEY(tenant_id,command_job_id,resource_version_after)
  REFERENCES job_events(tenant_id,job_id,job_version) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(tenant_id,membership_id,actor_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,policy_id,policy_version) REFERENCES policy_versions(tenant_id,policy_id,version),
 FOREIGN KEY(tenant_id,risk_assessment_id) REFERENCES risk_assessments(tenant_id,id),
 FOREIGN KEY(tenant_id,permit_id) REFERENCES server_approval_permits(tenant_id,id),
 CHECK(approval_version_before>0 AND approval_version_after=approval_version_before+1),
 CHECK(resource_version_before>0 AND resource_version_after=resource_version_before+1),
 CHECK((decision='APPROVE')=(risk_assessment_id IS NOT NULL)),
 CHECK((method='SERVER_POLICY')=(permit_id IS NOT NULL)),
 CHECK((method='SERVER_POLICY')=(actor_kind='POLICY')),
 CHECK(method<>'SERVER_POLICY' OR decision='APPROVE')
);
ALTER TABLE approvals ADD CONSTRAINT fk_approval_decision FOREIGN KEY(tenant_id,decision_id)
 REFERENCES approval_decisions(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['approvals','approval_decisions','server_approval_permits'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE POLICY requester_read ON approvals FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND requester_id=public.current_actor_id());
CREATE POLICY own_decision ON approval_decisions FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND (actor_id=public.current_actor_id() OR EXISTS(
  SELECT 1 FROM approvals a WHERE a.tenant_id=approval_decisions.tenant_id AND a.id=approval_decisions.approval_id)));
-- 白名单只通过精确审批上下文读取，不开放租户内其他配置/成员标识。

CREATE FUNCTION access.approval_payload(p_document jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
 SELECT p_document-ARRAY['status','version','updated_at','risk_summary','approval','execution_receipt','error','history'];
$$;
REVOKE ALL ON FUNCTION access.approval_payload(jsonb) FROM PUBLIC,app_login,app_auth;

CREATE FUNCTION access.approval_open(p_member uuid,p_user uuid,p_job uuid,p_version bigint,p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); job public.command_jobs; policy public.risk_policies;
 item public.approvals; payload jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 PERFORM access.job_state_context(p_member,p_user,p_job,'CONFIRM');
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job FOR UPDATE;
 IF job.status<>'PENDING_APPROVAL' OR job.version<>p_version OR job.target_type<>'AD_PLAN'
  OR job.claimed_at IS NOT NULL OR job.dispatch_started_at IS NOT NULL OR job.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'pending unclaimed resource required' USING ERRCODE='40001'; END IF;
 SELECT * INTO policy FROM public.risk_policies WHERE tenant_id=tenant;
 IF NOT FOUND OR policy.version<>(job.document#>>'{risk_summary,policy_version}')::bigint THEN
  RAISE EXCEPTION 'approval policy changed' USING ERRCODE='P0210'; END IF;
 payload:=access.approval_payload(job.document);
 SELECT * INTO item FROM public.approvals WHERE tenant_id=tenant AND command_job_id=p_job FOR UPDATE;
 IF FOUND THEN
  IF item.resource_version<>p_version OR item.intent_hash<>job.intent_hash OR item.payload<>payload
   OR item.policy_id<>policy.id OR item.policy_version<>policy.version THEN
   RAISE EXCEPTION 'approval frozen resource differs' USING ERRCODE='23505'; END IF;
  RETURN jsonb_build_object('approval',to_jsonb(item),'created',false);
 END IF;
 INSERT INTO public.approvals(id,tenant_id,biz_type,biz_id,command_job_id,requester_id,requester_membership_id,
  resource_version,policy_id,policy_version,intent_hash,payload,payload_digest,state)
 VALUES(p_id,tenant,'COMMAND_JOB',p_job,p_job,p_user,p_member,p_version,policy.id,policy.version,
  job.intent_hash,payload,encode(sha256(convert_to(payload::text,'UTF8')),'hex'),'PENDING') RETURNING * INTO item;
 RETURN jsonb_build_object('approval',to_jsonb(item),'created',true);
END $$;
REVOKE ALL ON FUNCTION access.approval_open(uuid,uuid,uuid,bigint,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.approval_open(uuid,uuid,uuid,bigint,uuid) TO app_login;

CREATE FUNCTION access.approval_context(p_member uuid,p_user uuid,p_job uuid,p_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); job public.command_jobs; item public.approvals;
 policy public.risk_policies;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 PERFORM access.job_state_context(p_member,p_user,p_job,'APPROVE');
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job FOR UPDATE;
 SELECT * INTO item FROM public.approvals WHERE tenant_id=tenant AND command_job_id=p_job FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'approval not registered' USING ERRCODE='P0002'; END IF;
 IF job.status<>'PENDING_APPROVAL' OR job.version<>p_version OR item.state<>'PENDING'
  OR item.resource_version<>p_version OR job.claimed_at IS NOT NULL OR job.dispatch_started_at IS NOT NULL
  OR job.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'approval resource version changed' USING ERRCODE='40001'; END IF;
 SELECT * INTO policy FROM public.risk_policies WHERE tenant_id=tenant;
 IF NOT FOUND OR (policy.id,policy.version) IS DISTINCT FROM (item.policy_id,item.policy_version)
  OR policy.version<>(job.document#>>'{risk_summary,policy_version}')::bigint THEN
  RAISE EXCEPTION 'approval policy changed' USING ERRCODE='P0210'; END IF;
 IF job.intent_hash<>item.intent_hash OR access.approval_payload(job.document)<>item.payload
  OR encode(sha256(convert_to(item.payload::text,'UTF8')),'hex')<>item.payload_digest THEN
  RAISE EXCEPTION 'approval intent differs' USING ERRCODE='22023'; END IF;
 RETURN jsonb_build_object('job',to_jsonb(job),'approval',to_jsonb(item));
END $$;
REVOKE ALL ON FUNCTION access.approval_context(uuid,uuid,uuid,bigint) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.approval_context(uuid,uuid,uuid,bigint) TO app_login;

CREATE FUNCTION access.approval_permit_valid(p_permit uuid,p_approval uuid,p_member uuid,p_user uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); permit public.server_approval_permits;
 item public.approvals; job public.command_jobs; delta numeric; before_value numeric;
BEGIN
 SELECT * INTO permit FROM public.server_approval_permits WHERE tenant_id=tenant AND id=p_permit
  AND active AND policy_actor_membership_id=p_member AND valid_from<=clock_timestamp()
  AND valid_until>clock_timestamp();
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO item FROM public.approvals WHERE tenant_id=tenant AND id=p_approval;
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=item.command_job_id;
 IF job.id IS NULL OR job.created_by=p_user OR permit.account_id IS DISTINCT FROM job.account_id
  OR permit.campaign_id IS DISTINCT FROM job.campaign_id OR permit.action IS DISTINCT FROM job.document->>'action_type'
  OR (permit.policy_id,permit.policy_version) IS DISTINCT FROM (item.policy_id,item.policy_version)
  OR NOT EXISTS(SELECT 1 FROM public.memberships m WHERE m.tenant_id=tenant AND m.id=p_member
   AND m.user_id=p_user AND m.status='ACTIVE')
  OR NOT EXISTS(SELECT 1 FROM public.risk_policies p JOIN public.policy_versions v
   ON (v.tenant_id,v.policy_id,v.version)=(p.tenant_id,p.id,p.version)
   WHERE p.tenant_id=tenant AND p.id=permit.policy_id AND p.version=permit.policy_version
    AND v.config->>'mode'='AUTOPILOT') THEN RETURN false; END IF;
 IF permit.action='PAUSE_AD_PLAN' THEN
  RETURN permit.important IS FALSE AND permit.classification_ref IS NOT NULL
   AND job.document#>>'{old_value,kind}'='BOOLEAN' AND job.document#>>'{old_value,value}'='true'
   AND job.document#>>'{new_value,kind}'='BOOLEAN' AND job.document#>>'{new_value,value}'='false';
 END IF;
 IF job.document#>>'{old_value,kind}' IS DISTINCT FROM 'MONEY'
  OR job.document#>>'{new_value,kind}' IS DISTINCT FROM 'MONEY'
  OR job.document#>>'{old_value,currency}' IS DISTINCT FROM 'CNY'
  OR job.document#>>'{new_value,currency}' IS DISTINCT FROM 'CNY' THEN RETURN false; END IF;
 before_value:=(job.document#>>'{old_value,amount_minor}')::numeric;
 delta:=before_value-(job.document#>>'{new_value,amount_minor}')::numeric;
 RETURN coalesce(before_value>0 AND delta>0 AND delta<=permit.max_change_minor
  AND delta*100<=before_value*permit.max_change_percent,false);
END $$;
REVOKE ALL ON FUNCTION access.approval_permit_valid(uuid,uuid,uuid,uuid) FROM PUBLIC,app_login,app_auth;

CREATE FUNCTION access.approval_decide(p_member uuid,p_user uuid,p_job uuid,p_version bigint,
 p_approval_version bigint,p_decision text,p_method text,p_risk uuid,p_permit uuid,p_document jsonb,
 p_id uuid,p_request uuid,p_comment text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); ctx jsonb; job public.command_jobs;
 item public.approvals; risk public.risk_assessments; decided public.approval_decisions;
 stamp timestamptz; updated jsonb; state_value text; proof text;
BEGIN
 ctx:=access.approval_context(p_member,p_user,p_job,p_version);
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job;
 SELECT * INTO item FROM public.approvals WHERE tenant_id=tenant AND id=(ctx#>>'{approval,id}')::uuid;
 IF p_decision IS NULL OR p_method IS NULL
  OR p_decision NOT IN ('APPROVE','REJECT') OR p_method NOT IN ('MANUAL','SERVER_POLICY')
  OR p_id IS NULL OR p_request IS NULL OR p_approval_version IS DISTINCT FROM item.version
  OR length(p_comment)>500 THEN RAISE EXCEPTION 'invalid approval input' USING ERRCODE='22023'; END IF;
 state_value:=CASE WHEN p_decision='APPROVE' THEN 'APPROVED' ELSE 'REJECTED' END;
 stamp:=(p_document->>'updated_at')::timestamptz;
 IF p_document#>>'{approval,state}' IS DISTINCT FROM state_value
  OR p_document#>>'{approval,actor_id}' IS DISTINCT FROM p_user::text
  OR p_document#>>'{approval,method}' IS DISTINCT FROM p_method
  OR p_document#>>'{approval,approved_version}' IS DISTINCT FROM p_version::text
  OR (p_document#>>'{approval,decided_at}')::timestamptz IS DISTINCT FROM stamp
  OR p_document->'execution_receipt' IS DISTINCT FROM job.document->'execution_receipt'
  OR p_document->'error' IS DISTINCT FROM job.document->'error' THEN
  RAISE EXCEPTION 'approval document differs' USING ERRCODE='22023'; END IF;
 IF p_decision='APPROVE' THEN
  SELECT * INTO risk FROM public.risk_assessments WHERE tenant_id=tenant AND id=p_risk AND job_id=p_job
   AND user_id=p_user AND membership_id=p_member AND stage='APPROVE' AND job_version=p_version
   AND policy_id=item.policy_id AND policy_version=item.policy_version AND checked_at=stamp
   AND expires_at>clock_timestamp() AND summary=p_document->'risk_summary'
   AND summary->>'result'='REVIEW_REQUIRED' AND summary->>'source'='SERVER'
   AND jsonb_array_length(summary->'checks')>0
   AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(summary->'checks') c WHERE c->>'passed' IS DISTINCT FROM 'true');
  IF NOT FOUND THEN RAISE EXCEPTION 'fresh actual approval risk required' USING ERRCODE='22023'; END IF;
 ELSE
  IF p_risk IS NOT NULL OR p_document->'risk_summary' IS DISTINCT FROM job.document->'risk_summary' THEN
   RAISE EXCEPTION 'rejection must preserve frozen risk' USING ERRCODE='22023'; END IF;
 END IF;
 IF p_method='SERVER_POLICY' THEN
  IF p_decision<>'APPROVE' OR p_permit IS NULL
   OR access.approval_permit_valid(p_permit,item.id,p_member,p_user) IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'human approval required or policy permit unavailable' USING ERRCODE='42501'; END IF;
 ELSIF p_permit IS NOT NULL THEN RAISE EXCEPTION 'manual approval cannot use policy permit' USING ERRCODE='22023'; END IF;
 INSERT INTO public.approval_decisions(id,tenant_id,approval_id,command_job_id,decision,method,actor_kind,
  actor_id,membership_id,request_id,approval_version_before,approval_version_after,
  resource_version_before,resource_version_after,intent_hash,policy_id,policy_version,
  risk_assessment_id,permit_id,comment,decided_at)
 VALUES(p_id,tenant,item.id,p_job,p_decision,p_method,CASE WHEN p_method='MANUAL' THEN 'HUMAN' ELSE 'POLICY' END,
  p_user,p_member,p_request,item.version,item.version+1,p_version,p_version+1,item.intent_hash,
  item.policy_id,item.policy_version,p_risk,p_permit,p_comment,stamp) RETURNING * INTO decided;
 UPDATE public.approvals SET state=state_value,version=version+1,decision_id=p_id,updated_at=stamp
  WHERE tenant_id=tenant AND id=item.id AND version=p_approval_version AND state='PENDING' RETURNING * INTO item;
 IF NOT FOUND THEN RAISE EXCEPTION 'approval already decided' USING ERRCODE='40001'; END IF;
 proof:=encode(sha256(convert_to('approval-decision|'||p_id::text,'UTF8')),'hex');
 updated:=access.job_apply_transition(p_member,p_user,p_job,p_version,p_decision,p_document,p_request,proof);
 RETURN jsonb_build_object('job',updated,'approval',to_jsonb(item),'decision',to_jsonb(decided));
END $$;
REVOKE ALL ON FUNCTION access.approval_decide(uuid,uuid,uuid,bigint,bigint,text,text,uuid,uuid,jsonb,uuid,uuid,text)
 FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.approval_decide(uuid,uuid,uuid,bigint,bigint,text,text,uuid,uuid,jsonb,uuid,uuid,text)
 TO app_login;

CREATE FUNCTION public.approval_transition_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item public.approvals; decision public.approval_decisions;
BEGIN
 -- 维护角色可迁移/布置受信技术输入；运行角色即使经SECURITY DEFINER也不能绕过本门。
 IF session_user IN ('postgres','app_migrate') OR NEW.status NOT IN ('APPROVED','REJECTED')
  OR (NEW.status=OLD.status AND NEW.document->'approval' IS NOT DISTINCT FROM OLD.document->'approval') THEN RETURN NEW; END IF;
 SELECT * INTO item FROM public.approvals WHERE tenant_id=NEW.tenant_id AND command_job_id=NEW.id
  AND resource_version=OLD.version AND state=NEW.status AND version=2;
 SELECT * INTO decision FROM public.approval_decisions WHERE tenant_id=NEW.tenant_id AND id=item.decision_id;
 IF item.id IS NULL OR decision.id IS NULL OR decision.command_job_id IS DISTINCT FROM NEW.id
  OR decision.resource_version_before<>OLD.version OR decision.resource_version_after<>NEW.version
  OR decision.actor_id=NEW.created_by OR decision.intent_hash<>NEW.intent_hash
  OR item.intent_hash<>NEW.intent_hash OR item.payload IS DISTINCT FROM access.approval_payload(NEW.document)
  OR NEW.document#>>'{approval,method}' IS DISTINCT FROM decision.method
  OR NEW.document#>>'{approval,actor_id}' IS DISTINCT FROM decision.actor_id::text
  OR NEW.document#>>'{approval,approved_version}' IS DISTINCT FROM OLD.version::text
  OR decision.actor_id IS DISTINCT FROM public.current_actor_id()
  OR (decision.method='SERVER_POLICY' AND
   access.approval_permit_valid(decision.permit_id,item.id,decision.membership_id,decision.actor_id) IS DISTINCT FROM true) THEN
  RAISE EXCEPTION 'durable real approval decision required' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.approval_transition_gate() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER approval_transition_gate BEFORE UPDATE ON command_jobs
 FOR EACH ROW EXECUTE FUNCTION public.approval_transition_gate();

CREATE FUNCTION access.approval_policy_context(p_member uuid,p_user uuid,p_job uuid,p_version bigint,p_permit uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE ctx jsonb; permit public.server_approval_permits;
BEGIN
 ctx:=access.approval_context(p_member,p_user,p_job,p_version);
 IF access.approval_permit_valid(p_permit,(ctx#>>'{approval,id}')::uuid,p_member,p_user) IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'policy permit unavailable; human approval required' USING ERRCODE='42501'; END IF;
 SELECT * INTO permit FROM public.server_approval_permits WHERE tenant_id=public.current_tenant_id() AND id=p_permit;
 RETURN ctx||jsonb_build_object('permit',to_jsonb(permit));
END $$;
REVOKE ALL ON FUNCTION access.approval_policy_context(uuid,uuid,uuid,bigint,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.approval_policy_context(uuid,uuid,uuid,bigint,uuid) TO app_login;

CREATE OR REPLACE FUNCTION access.job_approval_current(p_member uuid,p_user uuid,p_job uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE source jsonb; reviewer uuid; tenant bigint:=public.current_tenant_id(); qualified boolean;
 reviewer_member uuid; item public.approvals; decision public.approval_decisions; method_value text;
BEGIN
 source:=access.job_state_context(p_member,p_user,p_job,'CLAIM');
 method_value:=source#>>'{document,approval,method}';
 IF source#>>'{document,approval,state}' IS DISTINCT FROM 'APPROVED'
  OR method_value NOT IN ('MANUAL','SERVER_POLICY') THEN RETURN false; END IF;
 BEGIN reviewer:=(source#>>'{document,approval,actor_id}')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN RETURN false; END;
 IF reviewer IS NULL OR reviewer=p_user THEN RETURN false; END IF;
 SELECT m.id INTO reviewer_member FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  WHERE m.tenant_id=tenant AND m.user_id=reviewer AND m.status='ACTIVE'
   AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
    WHERE g.tenant_id=tenant AND g.membership_id=m.id AND rp.permission_code='task.approve')
   AND EXISTS(SELECT 1 FROM public.shops s JOIN public.entity_links l
    ON (l.tenant_id,l.shop_id)=(s.tenant_id,s.id)
    JOIN public.ad_accounts a ON (a.tenant_id,a.id)=(l.tenant_id,l.account_id)
    WHERE s.tenant_id=tenant AND s.id=(source->>'shop_id')::uuid
     AND a.id=(source->>'account_id')::uuid AND s.deleted_at IS NULL AND a.deleted_at IS NULL
     AND a.status IN ('BOUND','AUTHORIZED','DEGRADED'))
   AND EXISTS(SELECT 1 FROM public.shops s JOIN public.data_scopes ds ON ds.tenant_id=s.tenant_id
    WHERE s.tenant_id=tenant AND s.id=(source->>'shop_id')::uuid AND ds.membership_id=m.id
     AND (ds.permissions_mask IS NULL OR 'task.approve'=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id))
   AND EXISTS(SELECT 1 FROM public.ad_accounts a JOIN public.data_scopes ds ON ds.tenant_id=a.tenant_id
    WHERE a.tenant_id=tenant AND a.id=(source->>'account_id')::uuid AND ds.membership_id=m.id
     AND (ds.permissions_mask IS NULL OR 'task.approve'=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.account_id=a.id OR ds.entity_id=a.entity_id));
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO item FROM public.approvals WHERE tenant_id=tenant AND command_job_id=p_job;
 IF NOT FOUND THEN
  -- 旧受信维护布置的人工批准保留原资格核验；新运行写入已由强门要求真实决定。
  RETURN method_value='MANUAL';
 END IF;
 SELECT * INTO decision FROM public.approval_decisions WHERE tenant_id=tenant AND id=item.decision_id;
 IF decision.id IS NULL OR item.state<>'APPROVED' OR decision.decision<>'APPROVE'
  OR decision.method<>method_value OR decision.actor_id<>reviewer OR decision.membership_id<>reviewer_member
  OR item.intent_hash IS DISTINCT FROM source->>'intent_hash'
  OR item.payload IS DISTINCT FROM access.approval_payload(source->'document')
  OR item.resource_version::text IS DISTINCT FROM source#>>'{document,approval,approved_version}'
  OR NOT EXISTS(SELECT 1 FROM public.risk_policies p WHERE p.tenant_id=tenant AND p.id=item.policy_id
    AND p.version=item.policy_version) THEN RETURN false; END IF;
 RETURN method_value='MANUAL' OR access.approval_permit_valid(decision.permit_id,item.id,reviewer_member,reviewer) IS TRUE;
END $$;

ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition','risk.assess',
 'budget.initialize','budget.reserve','idempotency.start','idempotency.complete','idempotency.recover',
 'execution.prepare','execution.result','budget.finalize','budget.observe','budget.resolve','execution.recover',
 'approval.request','approval.manual','approval.policy'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK(object_type IN (
 'user','shop','member','role','project','conversation','message','job','platform_request',
 'oauth_state','authorization','sync_source','budget_ledger','idempotency_request','execution_order',
 'execution_receipt','budget_finalization','budget_discrepancy','execution_recovery','approval'));
