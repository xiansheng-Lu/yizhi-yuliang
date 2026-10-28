-- 不授予任何表写权限，不改变原actor RLS；唯一出口只返回逐列白名单。
CREATE FUNCTION access.execution_evidence_read(p_member uuid,p_user uuid,p_job uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); job public.command_jobs;
 result jsonb; collection text;
BEGIN
 IF p_user IS DISTINCT FROM public.current_actor_id()
 OR access.snapshot_member_version(p_member,p_user) IS NULL THEN
  RAISE EXCEPTION 'current snapshot reader required' USING ERRCODE='42501';
 END IF;
 SELECT * INTO job FROM public.command_jobs
 WHERE tenant_id=tenant AND id=p_job AND created_by=p_user;
 IF NOT FOUND THEN RAISE EXCEPTION 'evidence unavailable' USING ERRCODE='P0002'; END IF;
 -- 此切片只处理已接执行源AD_PLAN；人工场次任务没有广告执行证据模型。
 IF job.target_type IS DISTINCT FROM 'AD_PLAN' OR job.account_id IS NULL THEN
  RAISE EXCEPTION 'execution business source not integrated' USING ERRCODE='22023';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.campaigns c JOIN public.ad_accounts a
 ON (a.tenant_id,a.id)=(c.tenant_id,c.account_id)
 WHERE c.tenant_id=tenant AND c.id=job.campaign_id AND c.shop_id=job.shop_id
 AND c.account_id=job.account_id
 AND access.configuration_scope(a.id,c.shop_id,a.entity_id,'snapshot.read')) THEN
  RAISE EXCEPTION 'current object scope required' USING ERRCODE='42501';
 END IF;
 SELECT jsonb_build_object(
  'job_id',job.id,'job_status',job.status,'job_version',job.version,
  'job_updated_at',job.updated_at,'as_of',statement_timestamp(),
  'warnings',jsonb_build_array('INTERNAL_READ_MODEL_NO_HTTP_OR_FRONTEND_CONTRACT',
   'HISTORICAL_FACTS_NOT_CURRENT_PLATFORM_VALUES_OR_SEND_AUTHORIZATION',
   'READBACK_TRANSPORT_OBSERVATION_IS_NOT_PERSISTED_MATCH_CONCLUSION'),
  'orders',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at,x.id) FROM (
   SELECT o.id,o.status,o.current_attempt,o.version,o.created_at,o.updated_at
   FROM public.execution_orders o WHERE o.tenant_id=tenant AND o.command_job_id=job.id
   AND o.created_by=p_user AND o.biz_type='COMMAND_JOB' ORDER BY o.created_at,o.id LIMIT 1001
  ) x),'[]'::jsonb),
  'attempts',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.order_id,x.attempt_no) FROM (
   SELECT a.id,a.order_id,a.attempt_no,a.state,a.sent_at,a.finished_at,a.version
   FROM public.execution_attempts a JOIN public.execution_orders o
   ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id)
   WHERE o.tenant_id=tenant AND o.command_job_id=job.id AND o.created_by=p_user
   AND a.actor_id=p_user ORDER BY a.order_id,a.attempt_no LIMIT 1001
  ) x),'[]'::jsonb),
  'receipts',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at,x.id) FROM (
   SELECT r.id,r.attempt_id,r.source,r.accepted,r.evidence_level,r.applied,r.disposition,r.created_at
   FROM public.execution_receipts r JOIN public.execution_orders o
   ON (o.tenant_id,o.id)=(r.tenant_id,r.order_id)
   WHERE o.tenant_id=tenant AND o.command_job_id=job.id AND o.created_by=p_user
   AND r.created_by=p_user ORDER BY r.created_at,r.id LIMIT 1001
  ) x),'[]'::jsonb),
  'recoveries',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at,x.id) FROM (
   SELECT r.id,r.attempt_id,r.state,
    CASE WHEN r.outcome IN ('PENDING','NOT_SENT','TERMINAL','STALE_GUARD',
     'RECONCILIATION_UNRESOLVED','VERIFYING','UNKNOWN','AUTH_REVOKED','INTEGRITY_REQUIRED','RESUMED')
    THEN r.outcome ELSE 'UNRECOGNIZED' END AS outcome,
    r.checks,r.next_due,r.created_at,r.updated_at
   FROM public.execution_recoveries r WHERE r.tenant_id=tenant AND r.job_id=job.id
   AND r.actor_id=p_user ORDER BY r.created_at,r.id LIMIT 1001
  ) x),'[]'::jsonb),
  'recovery_evidence',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at,x.id) FROM (
   SELECT e.id,e.recovery_id,e.attempt_id,e.kind,e.evidence_level,e.network_attempts,e.created_at
   FROM public.execution_recovery_evidence e JOIN public.execution_recoveries r
   ON (r.tenant_id,r.id)=(e.tenant_id,e.recovery_id)
   WHERE e.tenant_id=tenant AND e.job_id=job.id AND r.actor_id=p_user
   ORDER BY e.created_at,e.id LIMIT 1001
  ) x),'[]'::jsonb),
  'readbacks',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at,x.id) FROM (
   SELECT p.id,p.execution_attempt_id AS attempt_id,p.created_at,
    raw.external_outcome,raw.created_at AS observed_at
   FROM public.platform_requests p JOIN public.execution_orders o
   ON (o.tenant_id,o.id)=(p.tenant_id,p.execution_id)
   LEFT JOIN LATERAL(SELECT v.external_outcome,v.created_at FROM public.platform_raw_payloads v
    WHERE (v.tenant_id,v.outbound_id)=(p.tenant_id,p.id) AND v.user_id=p_user
    AND v.phase IN ('RESPONSE','ERROR') ORDER BY v.id DESC LIMIT 1) raw ON true
   WHERE o.tenant_id=tenant AND o.command_job_id=job.id AND o.created_by=p_user
   AND p.user_id=p_user AND p.effect='READ' AND p.method='GET'
   ORDER BY p.created_at,p.id LIMIT 1001
  ) x),'[]'::jsonb),
  'verified_facts',coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.verified_at,x.id) FROM (
   SELECT f.id,f.action_type,f.verified_at FROM public.verified_action_facts f
   WHERE f.tenant_id=tenant AND f.job_id=job.id ORDER BY f.verified_at,f.id LIMIT 1001
  ) x),'[]'::jsonb)
 ) INTO result;
 FOREACH collection IN ARRAY ARRAY['orders','attempts','receipts','recoveries',
 'recovery_evidence','readbacks','verified_facts'] LOOP
  IF jsonb_array_length(result->collection)>1000 THEN
   RAISE EXCEPTION 'complete evidence collection exceeds 1000' USING ERRCODE='54000';
  END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(result->'recoveries') r
  WHERE r->>'outcome'='UNRECOGNIZED') THEN
  result:=jsonb_set(result,'{warnings}',(result->'warnings')||
   jsonb_build_array('UNRECOGNIZED_RECOVERY_OUTCOME_REDACTED'));
 END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION access.execution_evidence_read(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_evidence_read(uuid,uuid,uuid) TO app_login;
