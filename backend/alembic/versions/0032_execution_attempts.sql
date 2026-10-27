ALTER TABLE target_guard_claims ADD CONSTRAINT ux_claim_tenant UNIQUE(tenant_id,id);
ALTER TABLE target_guard_claims ADD COLUMN job_version bigint;
UPDATE target_guard_claims c SET job_version=(SELECT max(e.job_version) FROM job_events e
 WHERE e.tenant_id=c.tenant_id AND e.job_id=c.job_id AND e.request_id=c.request_id
  AND e.control_event='CLAIM' AND e.created_at<=c.claimed_at);
CREATE FUNCTION public.guard_claim_version() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 SELECT version INTO NEW.job_version FROM public.command_jobs WHERE tenant_id=NEW.tenant_id AND id=NEW.job_id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_claim_version() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER guard_claim_version BEFORE INSERT ON target_guard_claims FOR EACH ROW EXECUTE FUNCTION public.guard_claim_version();
CREATE TABLE execution_orders (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,biz_type varchar(30) NOT NULL
  CHECK(biz_type IN ('COMMAND_JOB','PLAN_CHANGE','CONFIG_TASK','BUDGET_REQUEST','PLATFORM_COMMAND')),
 biz_id uuid NOT NULL,command_job_id uuid,created_by uuid NOT NULL,membership_id uuid NOT NULL,
 requested_version bigint NOT NULL CHECK(requested_version>0),shop_id uuid NOT NULL,account_id uuid NOT NULL,
 provider varchar(20) NOT NULL,product_line varchar(30) NOT NULL,target_native_id varchar(128) NOT NULL,
 native_account_id varchar(64) NOT NULL,action_type varchar(30) NOT NULL,
 binding_id uuid NOT NULL,capability_version integer NOT NULL,authorization_id uuid NOT NULL,authorization_version bigint NOT NULL,
 context_digest varchar(64) NOT NULL,intent_hash varchar(64) NOT NULL,frozen_payload jsonb NOT NULL,
 operation_key varchar(80) NOT NULL,schema_digest varchar(64) NOT NULL,
 wire_digest varchar(64) NOT NULL,wire_projection jsonb NOT NULL,
 guard_id uuid NOT NULL,policy_version bigint NOT NULL,
 status varchar(20) NOT NULL CHECK(status IN ('PENDING_APPROVAL','PENDING','EXECUTING','VERIFYING','EFFECTIVE','FAILED','UNKNOWN','CANCELLED','PARTIAL','MANUAL')),
 current_attempt integer NOT NULL CHECK(current_attempt>0),version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,biz_type,biz_id),UNIQUE(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,membership_id,created_by) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,command_job_id,account_id) REFERENCES command_jobs(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,account_id,provider,product_line) REFERENCES ad_accounts(tenant_id,id,provider,product_line),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,authorization_id) REFERENCES provider_authorizations(tenant_id,id),
 FOREIGN KEY(tenant_id,guard_id) REFERENCES target_execution_guards(tenant_id,id),
 CHECK((biz_type='COMMAND_JOB' AND command_job_id=biz_id) OR (biz_type<>'COMMAND_JOB' AND command_job_id IS NULL))
);
CREATE TABLE execution_attempts (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,order_id uuid NOT NULL,attempt_no integer NOT NULL CHECK(attempt_no>0),
 guard_claim_id uuid NOT NULL,outbound_id uuid NOT NULL UNIQUE,platform_request_key uuid NOT NULL,
 business_version bigint NOT NULL CHECK(business_version>0),account_id uuid NOT NULL,
 actor_id uuid NOT NULL,membership_id uuid NOT NULL,
 binding_id uuid NOT NULL,capability_version integer NOT NULL,authorization_id uuid NOT NULL,authorization_version bigint NOT NULL,
 context_digest varchar(64) NOT NULL,operation_key varchar(80) NOT NULL,schema_digest varchar(64) NOT NULL,
 wire_digest varchar(64) NOT NULL,wire_projection jsonb NOT NULL,
 state varchar(16) NOT NULL CHECK(state IN ('PENDING','SENT','ACK_ACCEPTED','REJECTED','UNKNOWN')),
 dispatch_digest varchar(64) NOT NULL CHECK(dispatch_digest ~ '^[0-9a-f]{64}$'),
 dispatch_consumed_at timestamptz,dispatch_assessment_id uuid,sent_at timestamptz NOT NULL,
 finished_at timestamptz,version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,order_id,id),UNIQUE(tenant_id,order_id,attempt_no),
 UNIQUE(tenant_id,platform_request_key),FOREIGN KEY(tenant_id,order_id) REFERENCES execution_orders(tenant_id,id),
 FOREIGN KEY(tenant_id,guard_claim_id) REFERENCES target_guard_claims(tenant_id,id),
 FOREIGN KEY(tenant_id,order_id,account_id) REFERENCES execution_orders(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,membership_id,actor_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,authorization_id) REFERENCES provider_authorizations(tenant_id,id),
 FOREIGN KEY(dispatch_assessment_id) REFERENCES risk_assessments(id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['execution_orders','execution_attempts'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
 END LOOP;
END $$;
CREATE POLICY private_actor ON execution_orders FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND created_by=public.current_actor_id());
CREATE POLICY private_actor ON execution_attempts FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM execution_orders o
  WHERE o.tenant_id=execution_attempts.tenant_id AND o.id=execution_attempts.order_id));
GRANT SELECT ON execution_orders,execution_attempts TO app_login;
ALTER TABLE platform_requests ADD CONSTRAINT ck_execution_pair
 CHECK((execution_id IS NULL)=(execution_attempt_id IS NULL));
ALTER TABLE platform_requests ADD CONSTRAINT fk_execution_attempt
 FOREIGN KEY(tenant_id,execution_id,execution_attempt_id) REFERENCES execution_attempts(tenant_id,order_id,id);

CREATE FUNCTION access.execution_register(p_member uuid,p_user uuid,p_job uuid,p_original_version bigint,
 p_order uuid,p_attempt uuid,p_outbound uuid,p_platform_key uuid,p_guard uuid,p_fence bigint,p_owner uuid,
 p_binding uuid,p_auth uuid,p_context text,p_dispatch_digest text,p_operation text,p_schema text,
 p_wire_digest text,p_wire jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); job public.command_jobs; target public.campaigns;
 binding public.capability_bindings; auth_row public.provider_authorizations; claim public.target_guard_claims;
 native_account text; old_value bigint; new_value bigint;
BEGIN
 PERFORM access.job_state_context(p_member,p_user,p_job,'CLAIM');
 PERFORM access.target_lease_current(p_member,p_user,p_job,p_guard,p_owner,p_fence);
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job FOR UPDATE;
 IF job.status<>'EXECUTING' OR job.version<>p_original_version+1 OR job.claimed_at IS NULL
  OR job.dispatch_started_at IS NOT NULL OR p_context !~ '^[0-9a-f]{64}$'
  OR p_dispatch_digest !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'execution preparation conflict' USING ERRCODE='40001'; END IF;
 SELECT * INTO target FROM public.campaigns WHERE tenant_id=tenant AND id=job.campaign_id;
 SELECT * INTO binding FROM public.capability_bindings WHERE tenant_id=tenant AND id=p_binding
  AND account_id=job.account_id AND provider=target.provider AND product_line=target.product_line
  AND active AND operation_kind='WRITE' AND action=CASE job.document->>'action_type'
   WHEN 'CHANGE_DAILY_BUDGET' THEN 'budget' WHEN 'CHANGE_BID' THEN 'bid' WHEN 'SET_ROI_TARGET' THEN 'goal'
   WHEN 'PAUSE_AD_PLAN' THEN 'pause' WHEN 'RESUME_AD_PLAN' THEN 'resume' END;
 IF NOT FOUND THEN RAISE EXCEPTION 'write binding unavailable' USING ERRCODE='22023'; END IF;
 SELECT * INTO auth_row FROM public.provider_authorizations WHERE tenant_id=tenant AND id=p_auth
  AND status='ACTIVE' AND expires_at>clock_timestamp() AND provider=target.provider AND product_line=target.product_line
  AND (account_id IS NULL OR account_id=job.account_id);
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.authorization_account_links l WHERE l.tenant_id=tenant
  AND l.authorization_id=p_auth AND l.account_id=job.account_id) THEN
  RAISE EXCEPTION 'execution authorization unavailable' USING ERRCODE='22023'; END IF;
 IF job.document->>'action_type'='CHANGE_DAILY_BUDGET' THEN
  old_value:=(job.document#>>'{old_value,amount_minor}')::bigint;
  new_value:=(job.document#>>'{new_value,amount_minor}')::bigint;
  IF new_value>old_value AND NOT EXISTS(SELECT 1 FROM public.job_budget_holds h
   WHERE h.tenant_id=tenant AND h.job_id=p_job AND h.state='HELD' AND h.intent_hash=job.intent_hash
    AND h.policy_version=(job.document#>>'{risk_summary,policy_version}')::bigint
    AND EXISTS(SELECT 1 FROM public.budget_reservations r WHERE r.tenant_id=tenant AND r.hold_id=h.id
     AND r.state='HELD' AND r.amount_minor=new_value-old_value)) THEN
   RAISE EXCEPTION 'confirmed money reservation required' USING ERRCODE='22023'; END IF;
 END IF;
 SELECT * INTO claim FROM public.target_guard_claims WHERE tenant_id=tenant AND guard_id=p_guard
  AND fence=p_fence AND lease_owner=p_owner AND job_id=p_job;
 IF NOT FOUND OR claim.job_version IS DISTINCT FROM job.version THEN
  RAISE EXCEPTION 'guard claim unavailable' USING ERRCODE='40001'; END IF;
 SELECT native_id INTO native_account FROM public.ad_accounts WHERE tenant_id=tenant AND id=job.account_id;
 INSERT INTO public.execution_orders(id,tenant_id,biz_type,biz_id,command_job_id,created_by,membership_id,
  requested_version,shop_id,account_id,provider,product_line,target_native_id,native_account_id,action_type,
  binding_id,capability_version,authorization_id,authorization_version,context_digest,intent_hash,frozen_payload,
  guard_id,policy_version,status,current_attempt,operation_key,schema_digest,wire_digest,wire_projection)
 VALUES(p_order,tenant,'COMMAND_JOB',p_job,p_job,p_user,p_member,p_original_version,job.shop_id,job.account_id,
  target.provider,target.product_line,target.native_id,native_account,job.document->>'action_type',p_binding,
  binding.revision,p_auth,auth_row.version,p_context,job.intent_hash,
  job.document-ARRAY['status','version','updated_at','risk_summary','approval','execution_receipt','error','history'],
  p_guard,(job.document#>>'{risk_summary,policy_version}')::bigint,'EXECUTING',1,
  p_operation,p_schema,p_wire_digest,p_wire);
 INSERT INTO public.execution_attempts(id,tenant_id,order_id,attempt_no,guard_claim_id,outbound_id,
  platform_request_key,state,dispatch_digest,sent_at,business_version,account_id,actor_id,membership_id,
  binding_id,capability_version,authorization_id,authorization_version,context_digest,operation_key,
  schema_digest,wire_digest,wire_projection) VALUES(p_attempt,tenant,p_order,1,claim.id,
  p_outbound,p_platform_key,'SENT',p_dispatch_digest,clock_timestamp(),job.version,job.account_id,p_user,p_member,
  p_binding,binding.revision,p_auth,auth_row.version,p_context,p_operation,p_schema,p_wire_digest,p_wire);
 UPDATE public.command_jobs SET dispatch_started_at=clock_timestamp() WHERE tenant_id=tenant AND id=p_job;
 RETURN p_order;
END $$;
REVOKE ALL ON FUNCTION access.execution_register(uuid,uuid,uuid,bigint,uuid,uuid,uuid,uuid,uuid,bigint,uuid,uuid,uuid,text,text,text,text,text,jsonb) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_register(uuid,uuid,uuid,bigint,uuid,uuid,uuid,uuid,uuid,bigint,uuid,uuid,uuid,text,text,text,text,text,jsonb) TO app_login;

CREATE FUNCTION access.execution_authorize_dispatch(p_member uuid,p_user uuid,p_attempt uuid,p_secret text,p_assessment uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); attempt public.execution_attempts; item public.execution_orders;
 claim public.target_guard_claims; risk public.risk_assessments;
BEGIN
 SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=tenant AND id=p_attempt FOR UPDATE;
 SELECT * INTO item FROM public.execution_orders WHERE tenant_id=tenant AND id=attempt.order_id AND created_by=p_user;
 IF NOT FOUND OR item.biz_type<>'COMMAND_JOB' THEN RAISE EXCEPTION 'execution unavailable' USING ERRCODE='P0002'; END IF;
 PERFORM access.job_state_context(p_member,p_user,item.command_job_id,'CLAIM');
 SELECT * INTO claim FROM public.target_guard_claims WHERE tenant_id=tenant AND id=attempt.guard_claim_id;
 PERFORM access.target_lease_current(p_member,p_user,item.command_job_id,claim.guard_id,claim.lease_owner,claim.fence);
 IF attempt.state<>'SENT' OR item.status<>'EXECUTING' OR item.current_attempt<>attempt.attempt_no
  OR p_secret IS NULL OR attempt.dispatch_consumed_at IS NOT NULL
  OR attempt.dispatch_digest<>encode(sha256(convert_to(p_secret,'UTF8')),'hex') THEN
  RAISE EXCEPTION 'original attempt only permits lookup' USING ERRCODE='40001'; END IF;
 SELECT * INTO risk FROM public.risk_assessments WHERE tenant_id=tenant AND id=p_assessment
  AND job_id=item.command_job_id AND user_id=p_user AND stage='EXECUTE' AND expires_at>clock_timestamp()
  AND checked_at<=clock_timestamp() AND summary->>'result'='REVIEW_REQUIRED'
  AND policy_version=item.policy_version AND jsonb_array_length(summary->'checks')>0 AND NOT EXISTS(
   SELECT 1 FROM jsonb_array_elements(summary->'checks') c WHERE c->>'passed' IS DISTINCT FROM 'true');
 IF NOT FOUND THEN RAISE EXCEPTION 'fresh dispatch qualification required' USING ERRCODE='22023'; END IF;
 UPDATE public.execution_attempts SET dispatch_assessment_id=p_assessment WHERE id=p_attempt AND tenant_id=tenant;
 PERFORM set_config('app.execution_dispatch_secret',p_secret,true);
END $$;
REVOKE ALL ON FUNCTION access.execution_authorize_dispatch(uuid,uuid,uuid,text,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_authorize_dispatch(uuid,uuid,uuid,text,uuid) TO app_login;

CREATE FUNCTION access.assert_execution_wire(p_member uuid,p_user uuid,p_order uuid,p_attempt uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); item public.execution_orders; attempt public.execution_attempts;
 claim public.target_guard_claims; risk public.risk_assessments; job public.command_jobs; ready boolean; permission text;
BEGIN
 SELECT * INTO item FROM public.execution_orders WHERE tenant_id=tenant AND id=p_order AND created_by=p_user;
 IF NOT FOUND OR item.biz_type<>'COMMAND_JOB' THEN RAISE EXCEPTION 'execution unavailable' USING ERRCODE='P0002'; END IF;
 PERFORM access.job_state_context(p_member,p_user,item.command_job_id,'CLAIM');
 SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=tenant AND id=p_attempt AND order_id=p_order;
 IF NOT FOUND OR attempt.state<>'SENT' OR item.status<>'EXECUTING' OR item.current_attempt<>attempt.attempt_no THEN
  RAISE EXCEPTION 'attempt only permits lookup' USING ERRCODE='40001'; END IF;
 SELECT * INTO claim FROM public.target_guard_claims WHERE tenant_id=tenant AND id=attempt.guard_claim_id;
 PERFORM access.target_lease_current(p_member,p_user,item.command_job_id,claim.guard_id,claim.lease_owner,claim.fence);
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=item.command_job_id;
 IF job.status<>'EXECUTING' OR job.intent_hash<>item.intent_hash OR job.dispatch_started_at IS NULL
  OR (SELECT version FROM public.risk_policies WHERE tenant_id=tenant)<>item.policy_version
  OR NOT access.job_approval_current(p_member,p_user,item.command_job_id)
  OR NOT EXISTS(SELECT 1 FROM public.provider_authorizations a WHERE a.tenant_id=tenant
    AND a.id=attempt.authorization_id AND a.version=attempt.authorization_version
    AND a.status='ACTIVE' AND a.expires_at>clock_timestamp())
  OR NOT EXISTS(SELECT 1 FROM public.capability_bindings b JOIN public.catalog_head h
    ON h.catalog_sha256=b.catalog_sha256 WHERE b.tenant_id=tenant AND b.id=attempt.binding_id AND b.active
    AND b.revision=attempt.capability_version) THEN
  RAISE EXCEPTION 'execution eligibility changed' USING ERRCODE='42501'; END IF;
 SELECT count(*)=6 AND bool_and(result='PASS' AND checked_at<=clock_timestamp() AND expires_at>clock_timestamp())
  INTO ready FROM(SELECT DISTINCT ON(phase) * FROM public.capability_assertions
   WHERE tenant_id=tenant AND binding_id=attempt.binding_id ORDER BY phase,checked_at DESC,sequence DESC) phases;
 IF ready IS DISTINCT FROM true THEN RAISE EXCEPTION 'write evidence expired' USING ERRCODE='42501'; END IF;
 SELECT permission_code INTO permission FROM public.capability_bindings WHERE tenant_id=tenant AND id=attempt.binding_id;
 IF NOT EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
  ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id) WHERE g.tenant_id=tenant
   AND g.membership_id=p_member AND rp.permission_code=permission)
  OR NOT EXISTS(SELECT 1 FROM public.shops s JOIN public.data_scopes ds ON ds.tenant_id=s.tenant_id
   WHERE s.tenant_id=tenant AND s.id=item.shop_id AND ds.membership_id=p_member
    AND (ds.permissions_mask IS NULL OR permission=ANY(ds.permissions_mask))
    AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id))
  OR NOT EXISTS(SELECT 1 FROM public.ad_accounts a JOIN public.data_scopes ds ON ds.tenant_id=a.tenant_id
   WHERE a.tenant_id=tenant AND a.id=item.account_id AND ds.membership_id=p_member
    AND (ds.permissions_mask IS NULL OR permission=ANY(ds.permissions_mask))
    AND (ds.scope_type='ALL_TENANT' OR ds.account_id=a.id OR ds.entity_id=a.entity_id)) THEN
  RAISE EXCEPTION 'current write scope denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO risk FROM public.risk_assessments WHERE tenant_id=tenant AND id=attempt.dispatch_assessment_id
  AND job_id=item.command_job_id AND user_id=p_user AND job_version=job.version AND stage='EXECUTE'
  AND policy_version=item.policy_version AND checked_at<=clock_timestamp() AND expires_at>clock_timestamp()
  AND summary->>'result'='REVIEW_REQUIRED' AND jsonb_array_length(summary->'checks')>0
  AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(summary->'checks') c WHERE c->>'passed' IS DISTINCT FROM 'true');
 IF NOT FOUND THEN RAISE EXCEPTION 'dispatch check no longer current' USING ERRCODE='42501'; END IF;
END $$;
REVOKE ALL ON FUNCTION access.assert_execution_wire(uuid,uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.assert_execution_wire(uuid,uuid,uuid,uuid) TO app_login;

CREATE FUNCTION public.execution_root_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item public.execution_orders; attempt public.execution_attempts; given text;
BEGIN
 IF NEW.execution_id IS NULL THEN RETURN NEW; END IF;
 SELECT * INTO item FROM public.execution_orders WHERE tenant_id=NEW.tenant_id AND id=NEW.execution_id;
 SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=NEW.tenant_id
  AND id=NEW.execution_attempt_id AND order_id=NEW.execution_id FOR UPDATE;
 IF NOT FOUND OR (NEW.account_id,NEW.provider,NEW.product_line,NEW.target_native_id)
  IS DISTINCT FROM (item.account_id,item.provider,item.product_line,item.target_native_id) THEN
  RAISE EXCEPTION 'execution request context mismatch' USING ERRCODE='23514'; END IF;
 -- 独立只读允许晚到/隔离后留档，不给再次写入资格。
 IF NEW.effect='READ' THEN RETURN NEW; END IF;
 IF NEW.effect<>'WRITE' OR NEW.id<>attempt.outbound_id OR NEW.user_id<>item.created_by
  OR NEW.binding_id<>attempt.binding_id OR NEW.authorization_id<>attempt.authorization_id
  OR NEW.capability_version<>attempt.capability_version OR NEW.operation_key<>attempt.operation_key
  OR NEW.membership_id<>attempt.membership_id THEN
  RAISE EXCEPTION 'execution write context mismatch' USING ERRCODE='23514'; END IF;
 PERFORM access.assert_execution_wire(NEW.membership_id,NEW.user_id,item.id,attempt.id);
 given:=current_setting('app.execution_dispatch_secret',true);
 IF current_setting('app.execution_wire_digest',true) IS DISTINCT FROM attempt.wire_digest
  OR current_setting('app.execution_schema_digest',true) IS DISTINCT FROM attempt.schema_digest THEN
  RAISE EXCEPTION 'actual wire differs from frozen request' USING ERRCODE='23514'; END IF;
 IF given IS NULL OR attempt.dispatch_digest<>encode(sha256(convert_to(given,'UTF8')),'hex')
  OR attempt.dispatch_consumed_at IS NOT NULL THEN
  RAISE EXCEPTION 'one-use dispatch admission required' USING ERRCODE='42501'; END IF;
 UPDATE public.execution_attempts SET dispatch_consumed_at=clock_timestamp()
  WHERE tenant_id=NEW.tenant_id AND id=attempt.id AND dispatch_consumed_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'dispatch already consumed' USING ERRCODE='40001'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.execution_root_gate() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER execution_root_gate BEFORE INSERT ON platform_requests FOR EACH ROW EXECUTE FUNCTION public.execution_root_gate();

CREATE FUNCTION public.execution_network_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE root public.platform_requests;
BEGIN
 IF NEW.phase<>'ATTEMPT' THEN RETURN NEW; END IF;
 SELECT * INTO root FROM public.platform_requests WHERE tenant_id=NEW.tenant_id AND id=NEW.outbound_id;
 IF root.execution_id IS NULL OR root.effect<>'WRITE' THEN RETURN NEW; END IF;
 IF NEW.network_attempt<>1 THEN RAISE EXCEPTION 'execution write cannot be retried implicitly' USING ERRCODE='42501'; END IF;
 PERFORM access.assert_execution_wire(root.membership_id,root.user_id,root.execution_id,root.execution_attempt_id);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.execution_network_gate() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER execution_network_gate BEFORE INSERT ON platform_raw_payloads FOR EACH ROW EXECUTE FUNCTION public.execution_network_gate();

CREATE FUNCTION public.execution_attempt_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 IF to_jsonb(NEW)-ARRAY['state','dispatch_consumed_at','dispatch_assessment_id','finished_at','version']
  IS DISTINCT FROM to_jsonb(OLD)-ARRAY['state','dispatch_consumed_at','dispatch_assessment_id','finished_at','version'] THEN
  RAISE EXCEPTION 'attempt identity and frozen request cannot change' USING ERRCODE='22023'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.execution_attempt_immutable() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER execution_attempt_immutable BEFORE UPDATE ON execution_attempts
 FOR EACH ROW EXECUTE FUNCTION public.execution_attempt_immutable();

ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition','risk.assess',
 'budget.initialize','budget.reserve','idempotency.start','idempotency.complete','idempotency.recover','execution.prepare'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK(object_type IN (
 'user','shop','member','role','project','conversation','message','job','platform_request',
 'oauth_state','authorization','sync_source','budget_ledger','idempotency_request','execution_order'));
