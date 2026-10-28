ALTER TABLE outbox_events DROP CONSTRAINT outbox_events_event_type_check;
ALTER TABLE outbox_events ADD CONSTRAINT outbox_events_event_type_check CHECK(event_type IN (
 'yuliang.system.identity_probe','yuliang.system.schedule_recovery','yuliang.system.execution_recovery',
 'yuliang.system.analysis_accept','yuliang.system.approved_execution',
 'yuliang.execute.guard','yuliang.platform.guard','yuliang.ai_media.guard'));

CREATE TABLE approval_execution_events (
 tenant_id bigint NOT NULL,event_id uuid PRIMARY KEY,decision_id uuid NOT NULL,approval_id uuid NOT NULL,
 job_id uuid NOT NULL,approved_job_version bigint NOT NULL CHECK(approved_job_version>0),
 subject_user_id uuid NOT NULL,subject_membership_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,event_id),UNIQUE(tenant_id,decision_id),UNIQUE(tenant_id,job_id,approved_job_version),
 FOREIGN KEY(tenant_id,decision_id) REFERENCES approval_decisions(tenant_id,id),
 FOREIGN KEY(tenant_id,approval_id,job_id) REFERENCES approvals(tenant_id,id,command_job_id),
 FOREIGN KEY(tenant_id,job_id,approved_job_version) REFERENCES job_events(tenant_id,job_id,job_version)
  DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(tenant_id,subject_membership_id,subject_user_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,event_id) REFERENCES outbox_events(tenant_id,event_id)
  DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE approval_execution_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_execution_events FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON approval_execution_events TO app_owner USING(true) WITH CHECK(true);
REVOKE ALL ON approval_execution_events FROM PUBLIC,app_auth,app_login;

CREATE FUNCTION public.approval_execution_source_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 RAISE EXCEPTION 'approved event source immutable' USING ERRCODE='22023';
END $$;
REVOKE ALL ON FUNCTION public.approval_execution_source_immutable() FROM PUBLIC,app_auth,app_login;
CREATE TRIGGER approval_execution_source_immutable BEFORE UPDATE OR DELETE ON approval_execution_events
 FOR EACH ROW EXECUTE FUNCTION public.approval_execution_source_immutable();

CREATE FUNCTION access.approved_execution_payload(p_tenant bigint,p_event uuid,p_job uuid,
 p_member uuid,p_user uuid) RETURNS jsonb LANGUAGE sql IMMUTABLE
SET search_path=pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object('tenant_id',p_tenant,'event_id',p_event,'job_id',p_job,
  'membership_id',p_member,'user_id',p_user)
$$;
REVOKE ALL ON FUNCTION access.approved_execution_payload(bigint,uuid,uuid,uuid,uuid)
 FROM PUBLIC,app_auth,app_login;

CREATE FUNCTION public.approved_execution_outbox_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE source public.approval_execution_events; proof public.approval_decisions;
 item public.approvals; job public.command_jobs;
BEGIN
 IF TG_OP='UPDATE' AND OLD.event_type='yuliang.system.approved_execution' AND
  (NEW.event_id,NEW.tenant_id,NEW.aggregate_type,NEW.aggregate_id,NEW.event_type,NEW.payload)
  IS DISTINCT FROM
  (OLD.event_id,OLD.tenant_id,OLD.aggregate_type,OLD.aggregate_id,OLD.event_type,OLD.payload) THEN
  RAISE EXCEPTION 'approved event identity immutable' USING ERRCODE='22023';
 END IF;
 IF NEW.event_type<>'yuliang.system.approved_execution' THEN RETURN NEW; END IF;
 SELECT * INTO source FROM public.approval_execution_events
 WHERE tenant_id=NEW.tenant_id AND event_id=NEW.event_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'registered approval event required' USING ERRCODE='42501'; END IF;
 SELECT * INTO proof FROM public.approval_decisions
 WHERE tenant_id=source.tenant_id AND id=source.decision_id;
 SELECT * INTO item FROM public.approvals WHERE tenant_id=source.tenant_id AND id=source.approval_id;
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=source.tenant_id AND id=source.job_id;
 IF proof.id IS NULL OR proof.decision<>'APPROVE' OR item.id IS NULL OR job.id IS NULL
 OR proof.approval_id<>source.approval_id OR proof.command_job_id<>source.job_id
 OR proof.resource_version_after<>source.approved_job_version
 OR item.decision_id IS DISTINCT FROM source.decision_id OR item.state<>'APPROVED'
 OR item.requester_id<>source.subject_user_id
 OR item.requester_membership_id<>source.subject_membership_id
 OR job.created_by<>source.subject_user_id OR NEW.aggregate_type<>'command_job'
 OR NEW.aggregate_id<>source.job_id::text
 OR NEW.payload IS DISTINCT FROM access.approved_execution_payload(source.tenant_id,source.event_id,
    source.job_id,source.subject_membership_id,source.subject_user_id)
 OR (TG_OP='INSERT' AND (job.status<>'APPROVED' OR job.version<>source.approved_job_version)) THEN
  RAISE EXCEPTION 'approved event does not match original decision subject' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.approved_execution_outbox_guard() FROM PUBLIC,app_auth,app_login;
CREATE TRIGGER approved_execution_outbox_guard BEFORE INSERT OR UPDATE ON outbox_events
 FOR EACH ROW EXECUTE FUNCTION public.approved_execution_outbox_guard();

-- 原审批权限/风险/状态机保持原实现；旧函数不再是运行角色可调用的旁路。
ALTER FUNCTION access.approval_decide(uuid,uuid,uuid,bigint,bigint,text,text,uuid,uuid,jsonb,uuid,uuid,text)
 RENAME TO approval_decide_core_0037;
REVOKE ALL ON FUNCTION access.approval_decide_core_0037(
 uuid,uuid,uuid,bigint,bigint,text,text,uuid,uuid,jsonb,uuid,uuid,text) FROM PUBLIC,app_login,app_auth;
CREATE FUNCTION access.approval_decide(p_member uuid,p_user uuid,p_job uuid,p_version bigint,
 p_approval_version bigint,p_decision text,p_method text,p_risk uuid,p_permit uuid,p_document jsonb,
 p_id uuid,p_request uuid,p_comment text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE result jsonb; decided public.approval_decisions; item public.approvals;
 job public.command_jobs; event uuid; tenant bigint:=public.current_tenant_id();
BEGIN
 result:=access.approval_decide_core_0037(p_member,p_user,p_job,p_version,p_approval_version,
  p_decision,p_method,p_risk,p_permit,p_document,p_id,p_request,p_comment);
 IF p_decision='APPROVE' THEN
  SELECT * INTO decided FROM public.approval_decisions WHERE tenant_id=tenant AND id=p_id;
  SELECT * INTO item FROM public.approvals WHERE tenant_id=tenant AND id=decided.approval_id;
  SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job;
  IF decided.id IS NULL OR decided.decision<>'APPROVE' OR job.status<>'APPROVED'
   OR job.version<>decided.resource_version_after OR decided.command_job_id<>job.id
   OR item.requester_id<>job.created_by THEN
   RAISE EXCEPTION 'actual newly approved resource required' USING ERRCODE='22023';
  END IF;
  -- 标识用途，不充当鉴权令牌；不接收调用者event ID，重放仍走原版本冲突。
  event:=substr(encode(sha256(convert_to('approved-execution-v1|'||tenant::text||'|'||
   job.id::text||'|'||decided.resource_version_after::text||'|'||decided.id::text,'UTF8')),'hex'),1,32)::uuid;
  INSERT INTO public.approval_execution_events(tenant_id,event_id,decision_id,approval_id,job_id,
   approved_job_version,subject_user_id,subject_membership_id)
  VALUES(tenant,event,decided.id,item.id,job.id,decided.resource_version_after,
   item.requester_id,item.requester_membership_id);
  INSERT INTO public.outbox_events(event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
  VALUES(event,tenant,'command_job',job.id::text,'yuliang.system.approved_execution',
   access.approved_execution_payload(tenant,event,job.id,item.requester_membership_id,item.requester_id));
 END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION access.approval_decide(uuid,uuid,uuid,bigint,bigint,text,text,uuid,uuid,jsonb,uuid,uuid,text)
 FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.approval_decide(uuid,uuid,uuid,bigint,bigint,text,text,uuid,uuid,jsonb,uuid,uuid,text)
 TO app_login;

CREATE FUNCTION access.approved_execution_origin(p_member uuid,p_user uuid,p_event uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); source public.approval_execution_events;
 notice public.outbox_events; active boolean;
BEGIN
 IF p_user IS DISTINCT FROM public.current_actor_id() OR NOT EXISTS(
  SELECT 1 FROM public.memberships m WHERE m.tenant_id=tenant AND m.id=p_member
  AND m.user_id=p_user) THEN
  RAISE EXCEPTION 'original subject required' USING ERRCODE='42501';
 END IF;
 SELECT * INTO source FROM public.approval_execution_events WHERE tenant_id=tenant
  AND event_id=p_event AND subject_user_id=p_user AND subject_membership_id=p_member;
 IF NOT FOUND THEN RAISE EXCEPTION 'registered approved event unavailable' USING ERRCODE='P0002'; END IF;
 SELECT * INTO notice FROM public.outbox_events WHERE tenant_id=tenant AND event_id=p_event;
 IF NOT EXISTS(SELECT 1 FROM public.approval_decisions d JOIN public.approvals a
  ON (a.tenant_id,a.id)=(d.tenant_id,d.approval_id)
  JOIN public.command_jobs j ON (j.tenant_id,j.id)=(a.tenant_id,a.command_job_id)
  WHERE d.tenant_id=tenant AND d.id=source.decision_id AND d.decision='APPROVE'
  AND d.command_job_id=source.job_id AND d.resource_version_after=source.approved_job_version
  AND a.id=source.approval_id AND a.decision_id=source.decision_id AND a.state='APPROVED'
  AND a.requester_id=source.subject_user_id AND a.requester_membership_id=source.subject_membership_id
  AND j.created_by=source.subject_user_id) THEN
  RAISE EXCEPTION 'original approval source differs' USING ERRCODE='22023';
 END IF;
 IF notice.id IS NULL OR notice.event_type<>'yuliang.system.approved_execution'
 OR notice.aggregate_type<>'command_job' OR notice.aggregate_id<>source.job_id::text
 OR notice.payload IS DISTINCT FROM access.approved_execution_payload(tenant,p_event,source.job_id,
    source.subject_membership_id,source.subject_user_id) THEN
  RAISE EXCEPTION 'approved message origin mismatch' USING ERRCODE='22023';
 END IF;
 SELECT coalesce(m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
  AND t.status='ACTIVE' AND t.deleted_at IS NULL,false) INTO active
 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 JOIN public.tenants t ON t.id=m.tenant_id WHERE m.tenant_id=tenant AND m.id=p_member;
 RETURN jsonb_build_object('tenant_id',source.tenant_id,'event_id',source.event_id,
  'job_id',source.job_id,'approved_job_version',source.approved_job_version,
  'decision_id',source.decision_id,'approval_id',source.approval_id,
  'subject_user_id',source.subject_user_id,'subject_membership_id',source.subject_membership_id,
  'subject_active',active);
END $$;
REVOKE ALL ON FUNCTION access.approved_execution_origin(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.approved_execution_origin(uuid,uuid,uuid) TO app_login;

CREATE FUNCTION access.approved_execution_context(p_member uuid,p_user uuid,p_event uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE origin jsonb;
BEGIN
 origin:=access.approved_execution_origin(p_member,p_user,p_event);
 IF (origin->>'subject_active')::boolean IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'active original subject required' USING ERRCODE='42501';
 END IF;
 RETURN origin-'subject_active';
END $$;
REVOKE ALL ON FUNCTION access.approved_execution_context(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.approved_execution_context(uuid,uuid,uuid) TO app_login;
