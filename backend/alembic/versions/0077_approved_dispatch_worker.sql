CREATE TABLE approved_execution_dispatches (
 tenant_id bigint NOT NULL,event_id uuid NOT NULL,job_id uuid NOT NULL,
 subject_user_id uuid NOT NULL,subject_membership_id uuid NOT NULL,
 state text NOT NULL CHECK(state IN ('BLOCKED_EXECUTOR','BLOCKED_AUTH','ADMITTED','RECOVERY_REQUIRED','RECEIPT_RECORDED')),
 attempt_id uuid,reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,event_id),
 FOREIGN KEY(tenant_id,event_id) REFERENCES approval_execution_events(tenant_id,event_id),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id),
 FOREIGN KEY(tenant_id,subject_membership_id,subject_user_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,attempt_id) REFERENCES execution_attempts(tenant_id,id),
 CHECK(state<>'BLOCKED_EXECUTOR' OR attempt_id IS NULL),
 CHECK(state NOT IN ('ADMITTED','RECOVERY_REQUIRED','RECEIPT_RECORDED') OR attempt_id IS NOT NULL)
);
ALTER TABLE approved_execution_dispatches ENABLE ROW LEVEL SECURITY;
ALTER TABLE approved_execution_dispatches FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON approved_execution_dispatches TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY private_read ON approved_execution_dispatches FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND subject_user_id=public.current_actor_id());
GRANT SELECT ON approved_execution_dispatches TO app_login;

CREATE FUNCTION access.approved_dispatch_record(p_member uuid,p_user uuid,p_event uuid,p_job uuid,
 p_state text,p_attempt uuid,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); src public.approval_execution_events;
 item public.approved_execution_dispatches; stored public.outbox_events;
BEGIN
 IF p_state IS NULL OR p_state NOT IN ('BLOCKED_EXECUTOR','BLOCKED_AUTH','ADMITTED','RECOVERY_REQUIRED','RECEIPT_RECORDED')
 OR p_reason IS NOT NULL AND p_reason NOT IN ('EXECUTOR_UNAVAILABLE','CURRENT_AUTH_OR_STATE','RECOVERY_ONLY','RAW_RECEIPT')
 THEN RAISE EXCEPTION 'invalid dispatch fact' USING ERRCODE='22023'; END IF;
 IF (p_state='BLOCKED_EXECUTOR' AND p_reason IS DISTINCT FROM 'EXECUTOR_UNAVAILABLE')
 OR (p_state='BLOCKED_AUTH' AND p_reason IS DISTINCT FROM 'CURRENT_AUTH_OR_STATE')
 OR (p_state='ADMITTED' AND p_reason IS NOT NULL)
 OR (p_state='RECOVERY_REQUIRED' AND p_reason IS DISTINCT FROM 'RECOVERY_ONLY')
 OR (p_state='RECEIPT_RECORDED' AND p_reason IS DISTINCT FROM 'RAW_RECEIPT')
 THEN RAISE EXCEPTION 'dispatch reason mismatch' USING ERRCODE='22023'; END IF;
 SELECT * INTO src FROM public.approval_execution_events WHERE tenant_id=tenant AND event_id=p_event;
 IF NOT FOUND OR p_user IS DISTINCT FROM public.current_actor_id() OR src.subject_user_id<>p_user
 OR src.subject_membership_id<>p_member OR src.job_id<>p_job THEN RAISE EXCEPTION 'dispatch source unavailable' USING ERRCODE='42501'; END IF;
 PERFORM access.approved_execution_origin(p_member,p_user,p_event);
 SELECT * INTO stored FROM public.outbox_events WHERE tenant_id=tenant AND event_id=p_event;
 IF NOT FOUND OR stored.event_type<>'yuliang.system.approved_execution' OR stored.aggregate_type<>'command_job'
 OR stored.aggregate_id<>src.job_id::text OR stored.payload<>jsonb_build_object(
 'event_id',p_event::text,'tenant_id',tenant,'job_id',src.job_id::text,
 'membership_id',p_member::text,'user_id',p_user::text)
 THEN RAISE EXCEPTION 'dispatch event mismatch' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_event::text,760715));
 SELECT * INTO item FROM public.approved_execution_dispatches WHERE tenant_id=tenant AND event_id=p_event FOR UPDATE;
 IF FOUND AND item.state IN ('BLOCKED_EXECUTOR','BLOCKED_AUTH') THEN RETURN to_jsonb(item); END IF;
 IF p_state='BLOCKED_AUTH' AND p_attempt IS NULL THEN
  SELECT a.id INTO p_attempt FROM public.execution_orders o JOIN public.execution_attempts a
   ON (a.tenant_id,a.order_id,a.attempt_no)=(o.tenant_id,o.id,o.current_attempt)
   WHERE o.tenant_id=tenant AND o.command_job_id=src.job_id AND o.created_by=p_user
   AND o.membership_id=p_member AND o.requested_version=src.approved_job_version;
 END IF;
 IF p_attempt IS NOT NULL THEN
  IF NOT EXISTS(SELECT 1 FROM public.execution_attempts a JOIN public.execution_orders o
   ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id) WHERE a.tenant_id=tenant AND a.id=p_attempt
   AND o.command_job_id=src.job_id AND o.created_by=p_user AND o.membership_id=p_member
   AND o.requested_version=src.approved_job_version)
  THEN RAISE EXCEPTION 'dispatch attempt mismatch' USING ERRCODE='42501'; END IF;
  IF p_state='RECEIPT_RECORDED' AND NOT EXISTS(SELECT 1 FROM public.execution_receipts
   WHERE tenant_id=tenant AND attempt_id=p_attempt) THEN
   RAISE EXCEPTION 'real receipt required' USING ERRCODE='42501'; END IF;
  IF p_state='RECOVERY_REQUIRED' AND NOT EXISTS(SELECT 1 FROM public.execution_recoveries
   WHERE tenant_id=tenant AND attempt_id=p_attempt) THEN
   RAISE EXCEPTION 'real recovery required' USING ERRCODE='42501'; END IF;
  IF p_state='ADMITTED' THEN PERFORM access.approved_execution_context(p_member,p_user,p_event); END IF;
 ELSIF p_state<>'BLOCKED_AUTH' AND EXISTS(SELECT 1 FROM public.execution_orders WHERE tenant_id=tenant AND command_job_id=src.job_id) THEN
  RAISE EXCEPTION 'existing attempt requires recovery' USING ERRCODE='40001';
 END IF;
 IF item.event_id IS NOT NULL THEN
  IF item.attempt_id IS DISTINCT FROM p_attempt THEN RAISE EXCEPTION 'dispatch immutable attempt' USING ERRCODE='40001'; END IF;
  IF item.state='RECEIPT_RECORDED' OR item.state=p_state THEN RETURN to_jsonb(item); END IF;
  IF p_state NOT IN ('RECOVERY_REQUIRED','RECEIPT_RECORDED','BLOCKED_AUTH') THEN RAISE EXCEPTION 'dispatch state denied' USING ERRCODE='22023'; END IF;
  UPDATE public.approved_execution_dispatches SET state=p_state,reason=p_reason,updated_at=clock_timestamp()
   WHERE tenant_id=tenant AND event_id=p_event RETURNING * INTO item;
 ELSE
  INSERT INTO public.approved_execution_dispatches(tenant_id,event_id,job_id,subject_user_id,subject_membership_id,state,attempt_id,reason)
   VALUES(tenant,p_event,src.job_id,p_user,p_member,p_state,p_attempt,p_reason) RETURNING * INTO item;
 END IF;
 RETURN to_jsonb(item);
END $$;
REVOKE ALL ON FUNCTION access.approved_dispatch_record(uuid,uuid,uuid,uuid,text,uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.approved_dispatch_record(uuid,uuid,uuid,uuid,text,uuid,text) TO app_login;
