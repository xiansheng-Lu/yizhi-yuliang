ALTER TABLE command_jobs ADD COLUMN claimed_at timestamptz,
 ADD COLUMN dispatch_started_at timestamptz;
CREATE FUNCTION access.job_initial_only() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 IF current_user<>'app_owner' AND (NEW.document IS NULL OR NEW.status NOT IN ('DRAFT','BLOCKED','MANUAL_REQUIRED')) THEN
  RAISE EXCEPTION 'only complete initial applications may be inserted' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION access.job_initial_only() FROM PUBLIC,app_auth,app_login;
CREATE TRIGGER job_initial_only BEFORE INSERT ON command_jobs
 FOR EACH ROW EXECUTE FUNCTION access.job_initial_only();
ALTER TABLE job_events ADD COLUMN job_version bigint NOT NULL DEFAULT 1 CHECK(job_version>0),
 ADD COLUMN control_event varchar(30),ADD COLUMN proof_digest varchar(64),
 ADD CONSTRAINT ux_job_event_version UNIQUE(tenant_id,job_id,job_version);
CREATE TABLE job_transition_rules (
 event varchar(30) NOT NULL,from_status varchar(20) NOT NULL,to_status varchar(20) NOT NULL,
 detail varchar(300) NOT NULL,PRIMARY KEY(event,from_status)
);
INSERT INTO job_transition_rules VALUES
 ('CONFIRM','DRAFT','PENDING_APPROVAL','已确认，等待审批'),
 ('APPROVE','PENDING_APPROVAL','APPROVED','审批通过，等待执行'),
 ('REJECT','PENDING_APPROVAL','REJECTED','审批已驳回'),
 ('CANCEL','DRAFT','CANCELLED','尚未领取，申请已取消'),
 ('CANCEL','PENDING_APPROVAL','CANCELLED','尚未领取，申请已取消'),
 ('CANCEL','APPROVED','CANCELLED','尚未领取，申请已取消'),
 ('EXPIRE','DRAFT','EXPIRED','未领取的申请已过期'),
 ('EXPIRE','PENDING_APPROVAL','EXPIRED','未领取的申请已过期'),
 ('EXPIRE','APPROVED','EXPIRED','未领取的申请已过期'),
 ('CLAIM','APPROVED','EXECUTING','执行内核已领取'),
 ('ACCEPT','EXECUTING','VERIFYING','平台明确受理，等待独立核验'),
 ('REJECT_SEND','EXECUTING','FAILED','平台明确拒绝'),
 ('SUBMIT_UNKNOWN','EXECUTING','EXECUTING','提交结果未知，仅核对原请求'),
 ('MATCH','VERIFYING','SUCCEEDED','独立回读已匹配'),
 ('MISMATCH','VERIFYING','FAILED','核验窗口结束仍不匹配，待对账'),
 ('READ_UNKNOWN','VERIFYING','VERIFYING','受理后回读未知，继续核对'),
 ('CONFIRMED_NOT_EXECUTED','EXECUTING','FAILED','已按持久证据确认未执行'),
 ('CONFIRMED_NOT_EXECUTED','VERIFYING','FAILED','已按持久证据确认未执行'),
 ('SAFE_RETRY','FAILED','EXECUTING','显式安全重试，沿用原申请并新增尝试');
ALTER TABLE job_transition_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_transition_rules FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON job_transition_rules TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON job_transition_rules FOR SELECT TO app_login
 USING(public.current_tenant_id() IS NOT NULL);
GRANT SELECT ON job_transition_rules TO app_login;

-- 精确任务+动作的读取门，不提供越过任务范围的通用RLS查询。
CREATE FUNCTION access.job_state_context(p_member uuid,p_user uuid,p_job uuid,p_event text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); item public.command_jobs; permission text;
BEGIN
 permission:=CASE WHEN p_event IN ('APPROVE','REJECT') THEN 'task.approve' ELSE 'task.create' END;
 IF p_user IS DISTINCT FROM public.current_actor_id() OR NOT EXISTS(
  SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.tenants t ON t.id=m.tenant_id
  WHERE m.tenant_id=tenant AND m.id=p_member AND m.user_id=p_user AND m.status='ACTIVE'
   AND u.status='ACTIVE' AND u.deleted_at IS NULL AND t.status='ACTIVE' AND t.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
    WHERE g.tenant_id=tenant AND g.membership_id=m.id AND rp.permission_code=permission)
 ) THEN RAISE EXCEPTION 'job permission denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO item FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job AND document IS NOT NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'job unavailable' USING ERRCODE='P0002'; END IF;
 IF (permission='task.create' AND item.created_by<>p_user)
  OR (permission='task.approve' AND item.created_by=p_user) OR NOT EXISTS(
   SELECT 1 FROM public.shops s JOIN public.data_scopes ds ON ds.tenant_id=s.tenant_id
   WHERE s.tenant_id=tenant AND s.id=item.shop_id AND s.deleted_at IS NULL
    AND ds.membership_id=p_member AND (ds.permissions_mask IS NULL OR permission=ANY(ds.permissions_mask))
    AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id)
  ) OR (item.account_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM public.ad_accounts a JOIN public.data_scopes ds ON ds.tenant_id=a.tenant_id
   WHERE a.tenant_id=tenant AND a.id=item.account_id AND a.deleted_at IS NULL
    AND ds.membership_id=p_member AND (ds.permissions_mask IS NULL OR permission=ANY(ds.permissions_mask))
    AND (ds.scope_type='ALL_TENANT' OR ds.account_id=a.id OR ds.entity_id=a.entity_id)
  )) THEN RAISE EXCEPTION 'job scope denied' USING ERRCODE='42501'; END IF;
 RETURN to_jsonb(item);
END $$;
REVOKE ALL ON FUNCTION access.job_state_context(uuid,uuid,uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.job_state_context(uuid,uuid,uuid,text) TO app_login;

CREATE FUNCTION access.job_apply_transition(p_member uuid,p_user uuid,p_job uuid,p_version bigint,
 p_event text,p_document jsonb,p_request uuid,p_proof text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); before public.command_jobs;
 after public.command_jobs; rule public.job_transition_rules; stamp timestamptz;
 mutable text[]:=ARRAY['status','version','updated_at','risk_summary','approval','execution_receipt','error','history'];
BEGIN
 PERFORM access.job_state_context(p_member,p_user,p_job,p_event);
 SELECT * INTO before FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job FOR UPDATE;
 SELECT * INTO rule FROM public.job_transition_rules WHERE event=p_event AND from_status=before.status;
 IF NOT FOUND OR before.version<>p_version THEN
  RAISE EXCEPTION 'job state or version changed' USING ERRCODE='40001'; END IF;
 IF (p_document-mutable) IS DISTINCT FROM (before.document-mutable)
  OR p_document->>'status' IS DISTINCT FROM rule.to_status
  OR p_document->>'version' IS DISTINCT FROM (p_version+1)::text
  OR p_document#>>'{history,-1,detail}' IS DISTINCT FROM rule.detail
  OR p_document#>>'{history,-1,status}' IS DISTINCT FROM rule.to_status
  OR jsonb_array_length(p_document->'history') IS DISTINCT FROM jsonb_array_length(before.document->'history')+1
  OR ((p_document->'history')-(jsonb_array_length(p_document->'history')-1))
     IS DISTINCT FROM before.document->'history'
  OR p_request IS NULL OR (p_proof IS NOT NULL AND p_proof !~ '^[0-9a-f]{64}$') THEN
  RAISE EXCEPTION 'invalid transition document' USING ERRCODE='22023'; END IF;
 stamp:=(p_document->>'updated_at')::timestamptz;
 IF stamp<before.updated_at OR stamp>clock_timestamp() OR
  (p_document#>>'{history,-1,at}')::timestamptz IS DISTINCT FROM stamp THEN
  RAISE EXCEPTION 'invalid transition time' USING ERRCODE='22023'; END IF;
 -- 取消/过期/领取严格共享未领取且未发送谓词；一个条件更新成功后其他竞争者409。
 UPDATE public.command_jobs j SET status=rule.to_status,version=j.version+1,document=p_document,
  updated_at=stamp,claimed_at=CASE WHEN p_event='CLAIM' THEN stamp ELSE j.claimed_at END
 WHERE j.tenant_id=tenant AND j.id=p_job AND j.version=p_version AND j.status=rule.from_status
  AND (p_event NOT IN ('CANCEL','EXPIRE','CLAIM') OR
       (j.claimed_at IS NULL AND j.dispatch_started_at IS NULL))
  AND (p_event NOT IN ('CONFIRM','APPROVE','CLAIM','SAFE_RETRY') OR j.expires_at>clock_timestamp())
  AND (p_event<>'EXPIRE' OR j.expires_at<=clock_timestamp())
 RETURNING * INTO after;
 IF NOT FOUND THEN RAISE EXCEPTION 'job predicate changed' USING ERRCODE='40001'; END IF;
 INSERT INTO public.job_events(tenant_id,job_id,from_status,to_status,actor_id,request_id,
  reason_code,detail,created_at,job_version,control_event,proof_digest)
 VALUES(tenant,p_job,before.status,after.status,p_user,p_request,p_event,rule.detail,stamp,
  after.version,p_event,p_proof);
 RETURN to_jsonb(after);
END $$;
REVOKE ALL ON FUNCTION access.job_apply_transition(uuid,uuid,uuid,bigint,text,jsonb,uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.job_apply_transition(uuid,uuid,uuid,bigint,text,jsonb,uuid,text) TO app_login;
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition'));
