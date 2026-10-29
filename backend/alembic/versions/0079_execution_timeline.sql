CREATE FUNCTION access.execution_timeline_read(p_member uuid,p_user uuid,p_job uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); base jsonb; events jsonb; snapshots jsonb;
BEGIN
 -- 复用已验证原actor、snapshot.read、AD_PLAN及当前店铺/账户双scope门，不借CLAIM。
 base:=access.execution_evidence_read(p_member,p_user,p_job);
 WITH sources(kind,id,at,entry) AS (
  SELECT 'JOB_EVENT',e.id::text,e.created_at,jsonb_build_object(
   'job_version',e.job_version,'source_version',e.job_version,
   'before_status',e.from_status,'after_status',e.to_status)
  FROM public.job_events e WHERE e.tenant_id=tenant AND e.job_id=p_job
  UNION ALL
  SELECT 'APPROVAL',a.id::text,a.created_at,jsonb_build_object('job_version',NULL,
   'warnings',jsonb_build_array('HISTORICAL_VERSION_UNKNOWN'))
  FROM public.approvals a WHERE a.tenant_id=tenant AND a.command_job_id=p_job AND a.requester_id=p_user
  UNION ALL
  SELECT 'APPROVAL_DECISION',d.id::text,d.decided_at,jsonb_build_object(
   'job_version',d.resource_version_after,'source_version',d.approval_version_after,'decision',d.decision)
  FROM public.approval_decisions d JOIN public.approvals a
   ON (a.tenant_id,a.id)=(d.tenant_id,d.approval_id)
  WHERE d.tenant_id=tenant AND d.command_job_id=p_job AND a.requester_id=p_user
  UNION ALL
  SELECT 'ATTEMPT',a.id::text,a.sent_at,jsonb_build_object(
   'job_version',a.business_version,'attempt_id',a.id)
  FROM public.execution_attempts a JOIN public.execution_orders o
   ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id)
  WHERE a.tenant_id=tenant AND o.command_job_id=p_job AND o.created_by=p_user AND a.actor_id=p_user
  UNION ALL
  SELECT 'RECEIPT',r.id::text,r.created_at,jsonb_build_object(
   'attempt_id',r.attempt_id,'evidence_level',r.evidence_level,
   'accepted',r.accepted,'applied',r.applied,'disposition',r.disposition)
  FROM public.execution_receipts r JOIN public.execution_orders o
   ON (o.tenant_id,o.id)=(r.tenant_id,r.order_id)
  WHERE r.tenant_id=tenant AND o.command_job_id=p_job AND o.created_by=p_user AND r.created_by=p_user
  UNION ALL
  SELECT 'RECOVERY_EVIDENCE',e.id::text,e.created_at,jsonb_build_object(
   'job_version',e.job_version,'attempt_id',e.attempt_id,
   'recovery_kind',e.kind,'evidence_level',e.evidence_level)
  FROM public.execution_recovery_evidence e JOIN public.execution_recoveries r
   ON (r.tenant_id,r.id)=(e.tenant_id,e.recovery_id)
  WHERE e.tenant_id=tenant AND e.job_id=p_job AND r.actor_id=p_user
  UNION ALL
  SELECT 'READ_TRANSPORT',raw.id::text,raw.created_at,jsonb_build_object('attempt_id',p.execution_attempt_id)
  FROM public.platform_raw_payloads raw JOIN public.platform_requests p
   ON (p.tenant_id,p.id)=(raw.tenant_id,raw.outbound_id)
  JOIN public.execution_orders o ON (o.tenant_id,o.id)=(p.tenant_id,p.execution_id)
  WHERE p.tenant_id=tenant AND o.command_job_id=p_job AND o.created_by=p_user
   AND p.user_id=p_user AND raw.user_id=p_user AND p.effect='READ' AND p.method='GET'
   AND raw.phase IN ('RESPONSE','ERROR')
  UNION ALL
  SELECT 'VERIFIED_FACT',f.id::text,f.verified_at,jsonb_build_object('evidence_level','E2')
  FROM public.verified_action_facts f WHERE f.tenant_id=tenant AND f.job_id=p_job
  UNION ALL
  SELECT 'APPROVED_ORIGIN',e.event_id::text,e.created_at,jsonb_build_object('job_version',e.approved_job_version)
  FROM public.approval_execution_events e
  WHERE e.tenant_id=tenant AND e.job_id=p_job AND e.subject_user_id=p_user
  UNION ALL
  SELECT 'DISPATCH',d.event_id::text,d.created_at,jsonb_build_object('job_version',NULL,
   'attempt_id',d.attempt_id,'warnings',jsonb_build_array('HISTORICAL_VERSION_UNKNOWN'))
  FROM public.approved_execution_dispatches d
  WHERE d.tenant_id=tenant AND d.job_id=p_job AND d.subject_user_id=p_user
  UNION ALL
  SELECT 'HUMAN_NOTE',n.id::text,n.created_at,jsonb_build_object('attempt_id',n.attempt_id,
   'note_kind',n.note_kind,'evidence_level','UNVERIFIED_NOTE')
  FROM public.execution_manual_notes n WHERE n.tenant_id=tenant AND n.job_id=p_job AND n.actor_id=p_user
 ), bounded AS (SELECT * FROM sources ORDER BY at,kind COLLATE "C",id COLLATE "C" LIMIT 1001)
 SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object('source_kind',kind,
  'source_id',id,'at',at,'evidence_level','E0')||entry)
  ORDER BY at,kind COLLATE "C",id COLLATE "C"),'[]'::jsonb) INTO events FROM bounded;
 IF jsonb_array_length(events)>1000 THEN
  RAISE EXCEPTION 'complete timeline exceeds 1000 events' USING ERRCODE='54000'; END IF;

 WITH current_rows(kind,id,state,version,updated_at) AS (
  SELECT 'JOB',p_job::text,base->>'job_status',(base->>'job_version')::bigint,
   (base->>'job_updated_at')::timestamptz
  UNION ALL
  SELECT 'ORDER',x->>'id',x->>'status',(x->>'version')::bigint,(x->>'updated_at')::timestamptz
   FROM jsonb_array_elements(base->'orders') x
  UNION ALL
  SELECT 'ATTEMPT',x->>'id',x->>'state',(x->>'version')::bigint,NULL::timestamptz
   FROM jsonb_array_elements(base->'attempts') x
  UNION ALL
  SELECT 'RECOVERY',x->>'id',x->>'state',NULL::bigint,(x->>'updated_at')::timestamptz
   FROM jsonb_array_elements(base->'recoveries') x
  UNION ALL
  SELECT 'APPROVAL',a.id::text,a.state,a.version,a.updated_at FROM public.approvals a
   WHERE a.tenant_id=tenant AND a.command_job_id=p_job AND a.requester_id=p_user
  UNION ALL
  SELECT 'DISPATCH',d.event_id::text,d.state,NULL::bigint,d.updated_at FROM public.approved_execution_dispatches d
   WHERE d.tenant_id=tenant AND d.job_id=p_job AND d.subject_user_id=p_user
 ), bounded AS (SELECT * FROM current_rows ORDER BY kind COLLATE "C",id COLLATE "C" LIMIT 1001)
 SELECT coalesce(jsonb_agg(jsonb_build_object('source_kind',kind,'source_id',id,'state',state,
  'version',version,'updated_at',updated_at,'interpretation','CURRENT_SNAPSHOT_NOT_HISTORICAL_EVENT')
  ORDER BY kind COLLATE "C",id COLLATE "C"),'[]'::jsonb) INTO snapshots FROM bounded;
 IF jsonb_array_length(snapshots)>1000 THEN
  RAISE EXCEPTION 'complete timeline exceeds 1000 snapshots' USING ERRCODE='54000'; END IF;
 RETURN jsonb_build_object('job_id',p_job,'as_of',base->'as_of','events',events,
  'current_snapshots',snapshots,'warnings',jsonb_build_array(
  'INTERNAL_TIMELINE_NO_HTTP_OR_FRONTEND_CONTRACT','CURRENT_SNAPSHOTS_ARE_NOT_EVENT_HISTORY',
  'HUMAN_NOTES_ARE_UNVERIFIED'));
END $$;
REVOKE ALL ON FUNCTION access.execution_timeline_read(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_timeline_read(uuid,uuid,uuid) TO app_login;
