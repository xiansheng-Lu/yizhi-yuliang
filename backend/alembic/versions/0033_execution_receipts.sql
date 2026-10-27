ALTER TABLE platform_raw_payloads ADD CONSTRAINT ux_raw_tenant_outbound
 UNIQUE(tenant_id,outbound_id,id);
ALTER TABLE execution_attempts ADD COLUMN last_result_raw_id bigint;
CREATE OR REPLACE FUNCTION public.execution_attempt_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 IF to_jsonb(NEW)-ARRAY['state','dispatch_consumed_at','dispatch_assessment_id','finished_at','version','last_result_raw_id']
  IS DISTINCT FROM to_jsonb(OLD)-ARRAY['state','dispatch_consumed_at','dispatch_assessment_id','finished_at','version','last_result_raw_id'] THEN
  RAISE EXCEPTION 'attempt identity and frozen request cannot change' USING ERRCODE='22023'; END IF;
 RETURN NEW;
END $$;
CREATE TABLE execution_receipts (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,order_id uuid NOT NULL,attempt_id uuid NOT NULL,
 outbound_id uuid NOT NULL,raw_id bigint NOT NULL,created_by uuid NOT NULL,
 source varchar(10) NOT NULL CHECK(source IN ('PLATFORM','SYSTEM')),accepted boolean,
 evidence_level varchar(2) NOT NULL CHECK(evidence_level IN ('E0','E1')),
 applied boolean NOT NULL,disposition varchar(30) NOT NULL CHECK(disposition IN (
  'APPLIED','STALE_FENCE','STALE_ATTEMPT','STALE_VERSION','STALE_EVIDENCE','TERMINAL')),
 job_receipt jsonb,result jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,attempt_id,raw_id),
 FOREIGN KEY(tenant_id,order_id,attempt_id) REFERENCES execution_attempts(tenant_id,order_id,id),
 FOREIGN KEY(tenant_id,outbound_id,raw_id) REFERENCES platform_raw_payloads(tenant_id,outbound_id,id),
 CHECK((source='PLATFORM')=(job_receipt IS NOT NULL)),
 CHECK(source='PLATFORM' OR (accepted IS NULL AND evidence_level='E0')),
 CHECK(evidence_level<>'E1' OR accepted=false),CHECK(applied=(disposition='APPLIED'))
);
ALTER TABLE execution_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE execution_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON execution_receipts TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY private_actor ON execution_receipts FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND created_by=public.current_actor_id());
GRANT SELECT ON execution_receipts TO app_login;

-- 结果只允许消费同一尝试的持久原始记录，决策由此函数从事实推导，不接收accepted/source参数。
CREATE FUNCTION access.execution_result_context(p_member uuid,p_user uuid,p_attempt uuid,
 p_raw bigint,p_version bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); item public.execution_orders;
 attempt public.execution_attempts; root public.platform_requests; raw public.platform_raw_payloads;
 claim public.target_guard_claims; guard public.target_execution_guards; job public.command_jobs;
 receipt public.execution_receipts; origin text:='SYSTEM'; accepted boolean:=NULL;
 event text:='SUBMIT_UNKNOWN'; target_state text:='UNKNOWN'; attempt_state text:='UNKNOWN';
 disposition text:='APPLIED'; evidence text:='E0';
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=tenant AND id=p_attempt;
 SELECT * INTO item FROM public.execution_orders WHERE tenant_id=tenant AND id=attempt.order_id AND created_by=p_user;
 IF NOT FOUND OR item.biz_type<>'COMMAND_JOB' THEN RAISE EXCEPTION 'execution unavailable' USING ERRCODE='P0002'; END IF;
 PERFORM access.job_state_context(p_member,p_user,item.command_job_id,'CLAIM');
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=item.command_job_id FOR UPDATE;
 SELECT * INTO item FROM public.execution_orders WHERE tenant_id=tenant AND id=item.id FOR UPDATE;
 SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=tenant AND id=p_attempt FOR UPDATE;
 SELECT * INTO receipt FROM public.execution_receipts WHERE tenant_id=tenant AND attempt_id=p_attempt AND raw_id=p_raw;
 IF FOUND THEN RETURN jsonb_build_object('existing',receipt.result); END IF;
 SELECT * INTO root FROM public.platform_requests WHERE tenant_id=tenant AND id=attempt.outbound_id
  AND execution_id=item.id AND execution_attempt_id=attempt.id AND user_id=p_user AND effect='WRITE';
 IF NOT FOUND THEN RAISE EXCEPTION 'original execution request required' USING ERRCODE='P0002'; END IF;
 SELECT * INTO raw FROM public.platform_raw_payloads WHERE tenant_id=tenant AND id=p_raw
  AND outbound_id=root.id AND user_id=p_user;
 IF NOT FOUND THEN RAISE EXCEPTION 'original execution observation required' USING ERRCODE='P0002'; END IF;
 -- 未发生网络或无真实响应只能是SYSTEM/E0；200但解码未确认仍accepted=null。
 IF raw.phase='RESPONSE' AND raw.dispatch='RESPONDED' AND raw.network_attempt=1
  AND raw.response_digest IS NOT NULL AND raw.http_status IS NOT NULL AND EXISTS(
   SELECT 1 FROM public.platform_raw_payloads a WHERE a.tenant_id=tenant AND a.outbound_id=root.id
    AND a.phase='ATTEMPT' AND a.network_attempt=raw.network_attempt AND a.id<raw.id) THEN
  origin:='PLATFORM';
  IF raw.external_outcome='ACCEPTED' AND raw.category='ACCEPTED' AND raw.http_status BETWEEN 200 AND 299 THEN
   accepted:=true; event:='ACCEPT'; target_state:='VERIFYING'; attempt_state:='ACK_ACCEPTED';
  ELSIF raw.external_outcome='REJECTED' AND raw.http_status BETWEEN 200 AND 499
   AND raw.category IN ('RATE_LIMITED','PARAMETER_REJECTED','AUTH_REQUIRED','FORBIDDEN','PLATFORM_REJECTED') THEN
   accepted:=false; event:='REJECT_SEND'; target_state:='FAILED'; attempt_state:='REJECTED'; evidence:='E1';
  END IF;
 END IF;
 SELECT * INTO claim FROM public.target_guard_claims WHERE tenant_id=tenant AND id=attempt.guard_claim_id;
 SELECT * INTO guard FROM public.target_execution_guards WHERE tenant_id=tenant AND id=claim.guard_id FOR UPDATE;
 IF item.current_attempt<>attempt.attempt_no THEN disposition:='STALE_ATTEMPT';
 ELSIF guard.fence IS DISTINCT FROM claim.fence OR guard.job_id IS DISTINCT FROM item.command_job_id
  OR guard.lease_owner IS DISTINCT FROM claim.lease_owner OR guard.state NOT IN ('LEASED','QUARANTINED') THEN
  disposition:='STALE_FENCE';
 ELSIF item.status NOT IN ('EXECUTING','UNKNOWN') OR job.status<>'EXECUTING'
  OR attempt.state NOT IN ('SENT','UNKNOWN') THEN disposition:='TERMINAL';
 ELSIF attempt.last_result_raw_id IS NOT NULL AND raw.id<=attempt.last_result_raw_id THEN disposition:='STALE_EVIDENCE';
 ELSIF item.version IS DISTINCT FROM p_version THEN disposition:='STALE_VERSION';
 END IF;
 RETURN jsonb_build_object('order',to_jsonb(item),'attempt',to_jsonb(attempt),'job',to_jsonb(job),
  'raw',to_jsonb(raw),'source',origin,'accepted',accepted,'event',event,'targetState',target_state,
  'attemptState',attempt_state,'disposition',disposition,'evidenceLevel',evidence);
END $$;
REVOKE ALL ON FUNCTION access.execution_result_context(uuid,uuid,uuid,bigint,bigint) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_result_context(uuid,uuid,uuid,bigint,bigint) TO app_login;

CREATE FUNCTION access.execution_record_result(p_member uuid,p_user uuid,p_attempt uuid,p_raw bigint,
 p_version bigint,p_receipt uuid,p_document jsonb,p_request uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); ctx jsonb; jr jsonb; result jsonb; proof text;
 order_id uuid; job_id uuid; actual_job public.command_jobs; raw public.platform_raw_payloads;
 after_job jsonb; event text; stamp timestamptz;
BEGIN
 ctx:=access.execution_result_context(p_member,p_user,p_attempt,p_raw,p_version);
 IF ctx?'existing' THEN RETURN ctx->'existing'; END IF;
 order_id:=(ctx#>>'{order,id}')::uuid; job_id:=(ctx#>>'{order,command_job_id}')::uuid;
 SELECT * INTO actual_job FROM public.command_jobs WHERE tenant_id=tenant AND id=job_id;
 SELECT * INTO raw FROM public.platform_raw_payloads WHERE tenant_id=tenant AND id=p_raw;
 IF ctx->>'source'='PLATFORM' THEN
  jr:=jsonb_build_object('receipt_id',p_receipt::text,'source','PLATFORM','platform_receipt_id',NULL,
   'accepted',ctx->'accepted','received_at',raw.created_at,'verification',
   CASE WHEN ctx->>'accepted'='true' THEN 'PENDING' ELSE 'UNKNOWN' END,'observed_value',NULL,'verified_at',NULL);
 END IF;
 IF ctx->>'disposition'='APPLIED' THEN
  event:=ctx->>'event'; stamp:=(p_document->>'updated_at')::timestamptz;
  IF p_document IS NULL
   OR (CASE WHEN jr IS NULL THEN p_document->'execution_receipt' IS DISTINCT FROM 'null'::jsonb
     ELSE ((p_document->'execution_receipt')-'received_at') IS DISTINCT FROM (jr-'received_at')
       OR (p_document#>>'{execution_receipt,received_at}')::timestamptz IS DISTINCT FROM raw.created_at END)
   OR p_document->'risk_summary' IS DISTINCT FROM actual_job.document->'risk_summary'
   OR p_document->'approval' IS DISTINCT FROM actual_job.document->'approval'
   OR stamp<raw.created_at
   OR (CASE WHEN event='ACCEPT' THEN p_document->'error' IS DISTINCT FROM 'null'::jsonb ELSE
    p_document#>>'{error,code}' IS DISTINCT FROM CASE WHEN event='REJECT_SEND' THEN 'PLATFORM_REJECTED' ELSE 'RESULT_UNKNOWN' END
    OR p_document#>>'{error,retryable}' IS DISTINCT FROM 'false'
    OR (p_document#>>'{error,at}')::timestamptz IS DISTINCT FROM stamp END)
   THEN RAISE EXCEPTION 'result document differs from durable evidence' USING ERRCODE='22023'; END IF;
  jr:=nullif(p_document->'execution_receipt','null'::jsonb);
  proof:=encode(sha256(convert_to(p_attempt::text||'|'||p_raw::text||'|'||coalesce(raw.response_digest,''),'UTF8')),'hex');
  after_job:=access.job_apply_transition(p_member,p_user,job_id,actual_job.version,event,p_document,p_request,proof);
  UPDATE public.execution_orders SET status=ctx->>'targetState',version=version+1,updated_at=stamp
   WHERE tenant_id=tenant AND id=order_id;
  UPDATE public.execution_attempts SET state=ctx->>'attemptState',last_result_raw_id=p_raw,
   finished_at=CASE WHEN ctx->>'accepted' IS NOT NULL THEN stamp ELSE NULL END,version=version+1
   WHERE tenant_id=tenant AND id=p_attempt;
  IF ctx->>'targetState'='UNKNOWN' THEN
   UPDATE public.target_execution_guards SET state='QUARANTINED' WHERE tenant_id=tenant
    AND id=(ctx#>>'{order,guard_id}')::uuid;
  END IF;
 END IF;
 SELECT * INTO actual_job FROM public.command_jobs WHERE tenant_id=tenant AND id=job_id;
 result:=jsonb_build_object('receipt_id',p_receipt,'execution_id',order_id,'attempt_id',p_attempt,
  'raw_id',p_raw,'source',ctx->>'source','accepted',ctx->'accepted','evidence_level',ctx->>'evidenceLevel',
  'applied',ctx->>'disposition'='APPLIED','disposition',ctx->>'disposition',
  'execution_status',(SELECT status FROM public.execution_orders WHERE id=order_id AND tenant_id=tenant),
  'execution_version',(SELECT version FROM public.execution_orders WHERE id=order_id AND tenant_id=tenant),
  'job_status',actual_job.status,'job_version',actual_job.version);
 INSERT INTO public.execution_receipts(id,tenant_id,order_id,attempt_id,outbound_id,raw_id,created_by,
  source,accepted,evidence_level,applied,disposition,job_receipt,result)
 VALUES(p_receipt,tenant,order_id,p_attempt,raw.outbound_id,p_raw,p_user,ctx->>'source',
  (ctx->>'accepted')::boolean,ctx->>'evidenceLevel',ctx->>'disposition'='APPLIED',ctx->>'disposition',jr,result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION access.execution_record_result(uuid,uuid,uuid,bigint,bigint,uuid,jsonb,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_record_result(uuid,uuid,uuid,bigint,bigint,uuid,jsonb,uuid) TO app_login;

ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition','risk.assess',
 'budget.initialize','budget.reserve','idempotency.start','idempotency.complete','idempotency.recover',
 'execution.prepare','execution.result'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK(object_type IN (
 'user','shop','member','role','project','conversation','message','job','platform_request',
 'oauth_state','authorization','sync_source','budget_ledger','idempotency_request','execution_order','execution_receipt'));
