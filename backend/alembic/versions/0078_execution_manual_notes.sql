-- 独立人工陈述不是平台或恢复proof，不赋E1/E2/放款/重发资格。
ALTER TABLE execution_orders ADD CONSTRAINT ux_manual_note_order_job UNIQUE(tenant_id,id,command_job_id);
ALTER TABLE execution_receipts ADD CONSTRAINT ux_manual_note_receipt_attempt UNIQUE(tenant_id,attempt_id,id);
ALTER TABLE execution_recoveries ADD CONSTRAINT ux_manual_note_recovery_attempt UNIQUE(tenant_id,attempt_id,id);
CREATE TABLE execution_manual_notes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id bigint NOT NULL,job_id uuid NOT NULL,
 order_id uuid NOT NULL,attempt_id uuid NOT NULL,receipt_id uuid,recovery_id uuid,
 actor_id uuid NOT NULL,membership_id uuid NOT NULL,request_key uuid NOT NULL,
 payload_fingerprint varchar(64) NOT NULL CHECK(payload_fingerprint ~ '^[0-9a-f]{64}$'),
 note_kind text NOT NULL CHECK(note_kind IN ('FOLLOW_UP','SUPPORT_REFERENCE','MANUAL_REVIEW')),
 reference_code varchar(120) NOT NULL CHECK(reference_code ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$'),
 note_text varchar(2000) NOT NULL CHECK(length(btrim(note_text))>0),
 source text NOT NULL DEFAULT 'HUMAN_STATEMENT' CHECK(source='HUMAN_STATEMENT'),
 evidence_level text NOT NULL DEFAULT 'UNVERIFIED_NOTE' CHECK(evidence_level='UNVERIFIED_NOTE'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,actor_id,job_id,request_key),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id),
 FOREIGN KEY(tenant_id,order_id,job_id) REFERENCES execution_orders(tenant_id,id,command_job_id),
 FOREIGN KEY(tenant_id,order_id,attempt_id) REFERENCES execution_attempts(tenant_id,order_id,id),
 FOREIGN KEY(tenant_id,attempt_id,receipt_id) REFERENCES execution_receipts(tenant_id,attempt_id,id),
 FOREIGN KEY(tenant_id,attempt_id,recovery_id) REFERENCES execution_recoveries(tenant_id,attempt_id,id),
 FOREIGN KEY(tenant_id,membership_id,actor_id) REFERENCES memberships(tenant_id,id,user_id)
);
ALTER TABLE execution_manual_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE execution_manual_notes FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON execution_manual_notes TO app_owner USING(true) WITH CHECK(true);
REVOKE ALL ON execution_manual_notes FROM PUBLIC,app_auth,app_login;
CREATE FUNCTION public.execution_manual_note_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 RAISE EXCEPTION 'manual note history immutable' USING ERRCODE='22023';
END $$;
REVOKE ALL ON FUNCTION public.execution_manual_note_immutable() FROM PUBLIC,app_auth,app_login;
CREATE TRIGGER execution_manual_note_immutable BEFORE UPDATE OR DELETE ON execution_manual_notes
 FOR EACH ROW EXECUTE FUNCTION public.execution_manual_note_immutable();

-- 扩展审计允许码，不替换/遗漏前面迁移已发布的允许码。
DO $$ DECLARE expression text; BEGIN
 SELECT pg_get_expr(conbin,conrelid) INTO expression FROM pg_constraint
 WHERE conrelid='public.audit_logs'::regclass AND conname='audit_logs_action_check';
 EXECUTE 'ALTER TABLE public.audit_logs DROP CONSTRAINT audit_logs_action_check';
 EXECUTE 'ALTER TABLE public.audit_logs ADD CONSTRAINT audit_logs_action_check CHECK (('
  ||expression||') OR action=''execution.manual_note'')';
 SELECT pg_get_expr(conbin,conrelid) INTO expression FROM pg_constraint
 WHERE conrelid='public.audit_logs'::regclass AND conname='audit_logs_object_type_check';
 EXECUTE 'ALTER TABLE public.audit_logs DROP CONSTRAINT audit_logs_object_type_check';
 EXECUTE 'ALTER TABLE public.audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK (('
  ||expression||') OR object_type=''execution_manual_note'')';
END $$;

CREATE FUNCTION access.execution_manual_note_safe(p_text text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
 SELECT p_text IS NOT NULL AND normalize(p_text,NFKC) !~* (
  'bearer[[:space:]]+[^[:space:]]+|'
  '(access[_ -]?token|refresh[_ -]?token|api[_ -]?key|appsecret|password|passwd|token|secret|authorization|cookie|session|csrf|client[_ -]?secret|密钥|口令|密码|授权码)'
  ||'[[:space:]]*[:=：][[:space:]]*[^[:space:]]+|'
  ||'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|'
  ||'(sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}|-----BEGIN')
$$;
REVOKE ALL ON FUNCTION access.execution_manual_note_safe(text) FROM PUBLIC,app_auth,app_login;

CREATE FUNCTION access.execution_manual_note_scope(p_member uuid,p_user uuid,p_job uuid,
 p_permission text,p_lock boolean) RETURNS public.command_jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); job public.command_jobs;
BEGIN
 IF p_permission NOT IN ('task.create','snapshot.read') OR p_permission IS NULL
 OR p_user IS DISTINCT FROM public.current_actor_id() THEN
  RAISE EXCEPTION 'manual note scope denied' USING ERRCODE='42501'; END IF;
 IF p_lock THEN
  PERFORM 1 FROM public.memberships WHERE tenant_id=tenant AND id=p_member AND user_id=p_user FOR SHARE;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.tenants t ON t.id=m.tenant_id WHERE m.tenant_id=tenant AND m.id=p_member
  AND m.user_id=p_user AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
  AND t.status='ACTIVE' AND t.deleted_at IS NULL
  AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
   ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
   WHERE g.membership_id=m.id AND rp.permission_code=p_permission)) THEN
  RAISE EXCEPTION 'current manual note permission required' USING ERRCODE='42501'; END IF;
 IF p_lock THEN
  SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job AND created_by=p_user FOR SHARE;
 ELSE
  SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job AND created_by=p_user;
 END IF;
 IF job.id IS NULL THEN RAISE EXCEPTION 'manual note job unavailable' USING ERRCODE='P0002'; END IF;
 IF job.target_type IS DISTINCT FROM 'AD_PLAN' OR NOT EXISTS(
  SELECT 1 FROM public.campaigns c JOIN public.ad_accounts a ON (a.tenant_id,a.id)=(c.tenant_id,c.account_id)
  WHERE c.tenant_id=tenant AND c.id=job.campaign_id AND c.shop_id=job.shop_id AND c.account_id=job.account_id
  AND access.configuration_scope(a.id,c.shop_id,a.entity_id,p_permission)) THEN
  RAISE EXCEPTION 'current manual note object scope denied' USING ERRCODE='42501'; END IF;
 RETURN job;
END $$;
REVOKE ALL ON FUNCTION access.execution_manual_note_scope(uuid,uuid,uuid,text,boolean)
 FROM PUBLIC,app_auth,app_login;

CREATE FUNCTION access.execution_manual_note_projection(p_note public.execution_manual_notes)
RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object('id',p_note.id,'job_id',p_note.job_id,'attempt_id',p_note.attempt_id,
  'receipt_id',p_note.receipt_id,'recovery_id',p_note.recovery_id,'note_kind',p_note.note_kind,
  'reference_code',p_note.reference_code,'note_text',p_note.note_text,'source',p_note.source,
  'evidence_level',p_note.evidence_level,'created_at',p_note.created_at)
$$;
REVOKE ALL ON FUNCTION access.execution_manual_note_projection(public.execution_manual_notes)
 FROM PUBLIC,app_auth,app_login;

CREATE FUNCTION access.execution_manual_note_append(p_member uuid,p_user uuid,p_job uuid,p_input jsonb,
 p_request uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); job public.command_jobs; ord public.execution_orders;
 attempt public.execution_attempts; prior public.execution_manual_notes; inserted public.execution_manual_notes;
 digest text; key uuid; receipt uuid; recovery uuid;
BEGIN
 job:=access.execution_manual_note_scope(p_member,p_user,p_job,'task.create',true);
 IF jsonb_typeof(p_input) IS DISTINCT FROM 'object'
 OR jsonb_typeof(p_input->'note_text') IS DISTINCT FROM 'string'
 OR jsonb_typeof(p_input->'reference_code') IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION 'invalid manual statement shape' USING ERRCODE='22023'; END IF;
 p_input:=jsonb_set(jsonb_set(p_input,'{note_text}',to_jsonb(btrim(normalize(p_input->>'note_text',NFKC)))),
  '{reference_code}',to_jsonb(btrim(normalize(p_input->>'reference_code',NFKC))));
 IF p_request IS NULL OR jsonb_typeof(p_input) IS DISTINCT FROM 'object'
 OR NOT (p_input ?& ARRAY['request_key','attempt_id','receipt_id','recovery_id','note_kind','reference_code','note_text'])
 OR p_input-ARRAY['request_key','attempt_id','receipt_id','recovery_id','note_kind','reference_code','note_text']<>'{}'::jsonb
 OR jsonb_typeof(p_input->'note_text') IS DISTINCT FROM 'string'
 OR jsonb_typeof(p_input->'reference_code') IS DISTINCT FROM 'string'
 OR jsonb_typeof(p_input->'note_kind') IS DISTINCT FROM 'string'
 OR jsonb_typeof(p_input->'request_key') IS DISTINCT FROM 'string'
 OR jsonb_typeof(p_input->'attempt_id') IS DISTINCT FROM 'string'
 OR p_input->>'note_kind' NOT IN ('FOLLOW_UP','SUPPORT_REFERENCE','MANUAL_REVIEW')
 OR (p_input->>'reference_code') !~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$'
 OR length(p_input->>'note_text') NOT BETWEEN 1 AND 2000 OR length(btrim(p_input->>'note_text'))=0
 OR translate(p_input->>'note_text',E'\n\t','') ~ '[[:cntrl:]]'
 OR NOT access.execution_manual_note_safe(p_input->>'note_text')
 OR NOT access.execution_manual_note_safe(p_input->>'reference_code') THEN
  RAISE EXCEPTION 'invalid manual statement, no credentials accepted' USING ERRCODE='22023'; END IF;
 BEGIN
  key:=(p_input->>'request_key')::uuid;
  receipt:=(p_input->>'receipt_id')::uuid; recovery:=(p_input->>'recovery_id')::uuid;
  SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=tenant AND id=(p_input->>'attempt_id')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'invalid manual note reference' USING ERRCODE='22023'; END;
 SELECT * INTO ord FROM public.execution_orders WHERE tenant_id=tenant AND id=attempt.order_id
  AND command_job_id=job.id AND created_by=p_user AND membership_id=p_member AND biz_type='COMMAND_JOB';
 IF key IS NULL OR ord.id IS NULL OR attempt.actor_id<>p_user
 OR (receipt IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.execution_receipts r
  WHERE r.tenant_id=tenant AND r.id=receipt AND r.attempt_id=attempt.id AND r.order_id=ord.id))
 OR (recovery IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.execution_recoveries r
  WHERE r.tenant_id=tenant AND r.id=recovery AND r.attempt_id=attempt.id AND r.job_id=job.id)) THEN
  RAISE EXCEPTION 'original actual note references required' USING ERRCODE='42501'; END IF;
 digest:=encode(sha256(convert_to(jsonb_build_object('job',p_job,'input',p_input)::text,'UTF8')),'hex');
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text||'|'||p_user::text||'|'||p_job::text||'|'||key::text,780018));
 SELECT * INTO prior FROM public.execution_manual_notes
  WHERE tenant_id=tenant AND actor_id=p_user AND job_id=p_job AND request_key=key;
 IF FOUND THEN
  IF prior.payload_fingerprint<>digest THEN
   RAISE EXCEPTION 'manual note key payload conflict' USING ERRCODE='23505'; END IF;
  RETURN access.execution_manual_note_projection(prior);
 END IF;
 INSERT INTO public.execution_manual_notes(tenant_id,job_id,order_id,attempt_id,receipt_id,recovery_id,
  actor_id,membership_id,request_key,payload_fingerprint,note_kind,reference_code,note_text)
 VALUES(tenant,job.id,ord.id,attempt.id,receipt,recovery,p_user,p_member,key,digest,
  p_input->>'note_kind',p_input->>'reference_code',p_input->>'note_text') RETURNING * INTO inserted;
 INSERT INTO public.audit_logs(tenant_id,actor_id,action,object_type,object_id,after_hash,request_id)
 VALUES(tenant,p_user,'execution.manual_note','execution_manual_note',inserted.id::text,digest,p_request);
 RETURN access.execution_manual_note_projection(inserted);
END $$;
REVOKE ALL ON FUNCTION access.execution_manual_note_append(uuid,uuid,uuid,jsonb,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_manual_note_append(uuid,uuid,uuid,jsonb,uuid) TO app_login;

CREATE FUNCTION access.execution_manual_notes_read(p_member uuid,p_user uuid,p_job uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE result jsonb;
BEGIN
 PERFORM access.execution_manual_note_scope(p_member,p_user,p_job,'snapshot.read',false);
 SELECT coalesce(jsonb_agg(access.execution_manual_note_projection(n) ORDER BY n.created_at,n.id),'[]'::jsonb)
 INTO result FROM (SELECT * FROM public.execution_manual_notes WHERE tenant_id=public.current_tenant_id()
  AND job_id=p_job AND actor_id=p_user ORDER BY created_at,id LIMIT 1001) n;
 IF jsonb_array_length(result)>1000 THEN
  RAISE EXCEPTION 'manual note full list too large' USING ERRCODE='54000'; END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION access.execution_manual_notes_read(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_manual_notes_read(uuid,uuid,uuid) TO app_login;
