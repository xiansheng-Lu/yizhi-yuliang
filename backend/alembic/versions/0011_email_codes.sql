ALTER TABLE email_codes ADD COLUMN preauth_id uuid REFERENCES auth_challenges(id);
ALTER TABLE email_codes ADD COLUMN delivery_state varchar(8) NOT NULL DEFAULT 'PENDING'
 CHECK(delivery_state IN ('PENDING','ACCEPTED','FAILED'));
ALTER TABLE email_codes ADD COLUMN delivery_accepted_at timestamptz;
ALTER TABLE email_codes ADD COLUMN invalidated_at timestamptz;
UPDATE email_codes SET invalidated_at=statement_timestamp() WHERE preauth_id IS NULL;
GRANT SELECT,INSERT ON public.email_codes TO auth_fn_owner;
GRANT UPDATE(verified_at,consumed_at,attempts,delivery_state,delivery_accepted_at,invalidated_at)
 ON public.email_codes TO auth_fn_owner;

SET LOCAL ROLE auth_fn_owner;
-- 邮件重发使用稳定摘要行的滑动60秒窗口，不能在分钟边界绕过；密码/验证保持原5分钟桶。
CREATE OR REPLACE FUNCTION auth.rate_limit(p_digest text,p_kind text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE start_at timestamptz; attempts integer; until_at timestamptz;
BEGIN
 IF p_digest !~ '^[0-9a-f]{64}$' OR p_kind NOT IN ('PASSWORD','EMAIL_SEND','EMAIL_VERIFY')
 THEN RAISE EXCEPTION 'invalid rate bucket' USING ERRCODE='22023'; END IF;
 IF p_kind='EMAIL_SEND' THEN
  INSERT INTO public.auth_rate_limits(key_digest,bucket_kind,window_start,counter,blocked_until)
  VALUES(p_digest,p_kind,to_timestamp(0),1,statement_timestamp()+interval '60 seconds')
  ON CONFLICT(key_digest,bucket_kind,window_start) DO UPDATE SET
   counter=CASE WHEN public.auth_rate_limits.blocked_until IS NULL
     OR public.auth_rate_limits.blocked_until<=statement_timestamp() THEN 1 ELSE 2 END,
   blocked_until=CASE WHEN public.auth_rate_limits.blocked_until IS NULL
     OR public.auth_rate_limits.blocked_until<=statement_timestamp()
     THEN statement_timestamp()+interval '60 seconds' ELSE public.auth_rate_limits.blocked_until END
  RETURNING counter,blocked_until INTO attempts,until_at;
  IF attempts=1 THEN RETURN 0; END IF;
  RETURN greatest(1,ceil(extract(epoch FROM until_at-statement_timestamp()))::integer);
 END IF;
 start_at:=to_timestamp(floor(extract(epoch FROM statement_timestamp())/300)*300);
 INSERT INTO public.auth_rate_limits(key_digest,bucket_kind,window_start,counter)
 VALUES(p_digest,p_kind,start_at,1)
 ON CONFLICT(key_digest,bucket_kind,window_start) DO UPDATE
 SET counter=least(public.auth_rate_limits.counter+1,6)
 RETURNING counter INTO attempts;
 IF attempts>5 THEN
  RETURN greatest(1,ceil(extract(epoch FROM start_at+interval '300 seconds'-statement_timestamp()))::integer);
 END IF;
 RETURN 0;
END; $$;
CREATE OR REPLACE FUNCTION auth.record_event(p_email text,p_outcome text,p_request text,p_ip text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF p_outcome NOT IN ('PASSWORD_REJECTED','PASSWORD_RATE_LIMITED','EMAIL_REJECTED',
   'EMAIL_RATE_LIMITED','EMAIL_CODE_REQUESTED','EMAIL_DELIVERY_FAILED')
   OR p_email !~ '^[0-9a-f]{64}$' OR p_ip !~ '^[0-9a-f]{64}$'
   OR length(p_request) NOT BETWEEN 1 AND 64
 THEN RAISE EXCEPTION 'invalid auth event' USING ERRCODE='22023'; END IF;
 INSERT INTO public.auth_events(outcome,email_digest,request_id,ip_digest)
 VALUES(p_outcome,p_email,p_request,p_ip);
END; $$;

CREATE FUNCTION auth.issue_challenge(p_email text,p_digest text,p_preauth text,p_csrf text)
RETURNS TABLE(challenge_id uuid,expires_at timestamptz,retry_after integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE preauth uuid; created uuid; expiry timestamptz; maximum integer; wait_seconds integer;
BEGIN
 IF p_email IS NULL OR length(p_email)>254 OR p_email !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'
   OR p_digest !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'invalid mail challenge' USING ERRCODE='22023'; END IF;
 SELECT id INTO preauth FROM public.auth_challenges WHERE cookie_token_hash=p_preauth
  AND csrf_token_hash=p_csrf AND revoked_at IS NULL AND auth_challenges.expires_at>statement_timestamp()
  FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'preauth denied' USING ERRCODE='42501'; END IF;
 wait_seconds:=greatest(
   auth.rate_limit(current_setting('app.email_digest',true),'EMAIL_SEND'),
   auth.rate_limit(current_setting('app.ip_digest',true),'EMAIL_SEND'));
 IF wait_seconds>0 THEN
  PERFORM auth.record_event(current_setting('app.email_digest',true),'EMAIL_RATE_LIMITED',
    current_setting('app.request_id',true),current_setting('app.ip_digest',true));
  RETURN QUERY SELECT NULL::uuid,NULL::timestamptz,wait_seconds; RETURN; END IF;
 created:=nullif(current_setting('app.email_challenge_id',true),'')::uuid;
 maximum:=coalesce(nullif(current_setting('app.email_daily_limit',true),'')::integer,10);
 IF created IS NULL OR maximum NOT BETWEEN 1 AND 100 THEN
  RAISE EXCEPTION 'server mail policy missing' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('email-code:'||lower(p_email),1));
 IF (SELECT count(*) FROM public.email_codes WHERE lower(email)=lower(p_email)
     AND created_at>=date_trunc('day',statement_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')>=maximum THEN
  PERFORM auth.record_event(current_setting('app.email_digest',true),'EMAIL_RATE_LIMITED',
    current_setting('app.request_id',true),current_setting('app.ip_digest',true));
  RETURN QUERY SELECT NULL::uuid,NULL::timestamptz,3600; RETURN; END IF;
 UPDATE public.email_codes SET invalidated_at=statement_timestamp()
  WHERE lower(email)=lower(p_email) AND consumed_at IS NULL AND invalidated_at IS NULL;
 expiry:=statement_timestamp()+interval '5 minutes';
 INSERT INTO public.email_codes(id,email,purpose,code_hash,expires_at,preauth_id,max_attempts)
 VALUES(created,lower(p_email),'LOGIN',p_digest,expiry,preauth,5);
 PERFORM auth.record_event(current_setting('app.email_digest',true),'EMAIL_CODE_REQUESTED',
   current_setting('app.request_id',true),current_setting('app.ip_digest',true));
 RETURN QUERY SELECT created,expiry,60;
END; $$;

-- 内部投递结果守卫：只按预认证绑定改本挑战，不开放验证码摘要或基础表。
CREATE FUNCTION auth.email_delivery(p_challenge uuid,p_accepted boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 UPDATE public.email_codes e SET delivery_state=CASE WHEN p_accepted THEN 'ACCEPTED' ELSE 'FAILED' END,
  delivery_accepted_at=CASE WHEN p_accepted THEN statement_timestamp() ELSE NULL END,
  invalidated_at=CASE WHEN p_accepted THEN e.invalidated_at ELSE statement_timestamp() END
 WHERE e.id=p_challenge AND e.delivery_state='PENDING' AND e.preauth_id IN (
   SELECT id FROM public.auth_challenges WHERE cookie_token_hash=current_setting('app.preauth_hash',true)
     AND csrf_token_hash=current_setting('app.preauth_csrf_hash',true));
 RETURN FOUND;
END; $$;

CREATE FUNCTION auth.email_session(p_challenge uuid,p_digest text,p_member uuid,p_token text,p_csrf text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE preauth public.auth_challenges%ROWTYPE; code public.email_codes%ROWTYPE;
 member public.memberships%ROWTYPE; created uuid;
BEGIN
 -- 全部邮件函数先锁预认证挑战，再锁邮件码，避免消费/重发的反向锁序。
 SELECT * INTO preauth FROM public.auth_challenges
 WHERE cookie_token_hash=current_setting('app.preauth_hash',true)
   AND csrf_token_hash=current_setting('app.preauth_csrf_hash',true)
   AND revoked_at IS NULL AND expires_at>statement_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO code FROM public.email_codes WHERE id=p_challenge FOR UPDATE;
 IF NOT FOUND OR code.preauth_id IS DISTINCT FROM preauth.id OR code.purpose<>'LOGIN'
  OR code.delivery_state<>'ACCEPTED' OR code.invalidated_at IS NOT NULL
  OR code.consumed_at IS NOT NULL OR code.expires_at<=statement_timestamp()
  OR code.attempts>=code.max_attempts THEN RETURN NULL; END IF;
 UPDATE public.email_codes SET attempts=attempts+1 WHERE id=code.id;
 IF p_digest IS NULL OR code.code_hash<>p_digest
    OR lower(code.email) IS DISTINCT FROM current_setting('app.login_email',true)
 THEN RETURN NULL; END IF;
 SELECT m.* INTO member FROM public.users u JOIN public.memberships m ON m.user_id=u.id
  JOIN public.tenants t ON t.id=m.tenant_id WHERE lower(u.email)=lower(code.email)
  AND u.status='ACTIVE' AND u.deleted_at IS NULL AND m.status='ACTIVE'
  AND t.status='ACTIVE' AND t.deleted_at IS NULL AND (p_member IS NULL OR m.id=p_member)
  ORDER BY m.tenant_id,m.id LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF p_token !~ '^[0-9a-f]{64}$' OR p_csrf !~ '^[0-9a-f]{64}$'
   OR current_setting('app.email_digest',true) !~ '^[0-9a-f]{64}$'
 THEN RAISE EXCEPTION 'invalid session input' USING ERRCODE='22023'; END IF;
 UPDATE public.auth_challenges SET revoked_at=statement_timestamp() WHERE id=preauth.id;
 INSERT INTO public.sessions(token_hash,csrf_token_hash,tenant_id,user_id,membership_id,
   authz_version,expires_at,absolute_expires_at)
 VALUES(p_token,p_csrf,member.tenant_id,member.user_id,member.id,member.authz_version,
   now()+interval '2 hours',now()+interval '7 days') RETURNING id INTO created;
 UPDATE public.email_codes SET verified_at=statement_timestamp(),consumed_at=statement_timestamp()
 WHERE id=code.id;
 INSERT INTO public.auth_events(tenant_id,user_id,outcome,email_digest,request_id,ip_digest)
 VALUES(member.tenant_id,member.user_id,'EMAIL_ACCEPTED',current_setting('app.email_digest',true),
   current_setting('app.request_id',true),current_setting('app.ip_digest',true));
 RETURN created;
END; $$;
REVOKE ALL ON FUNCTION auth.issue_challenge(text,text,text,text),auth.email_delivery(uuid,boolean),
 auth.email_session(uuid,text,uuid,text,text) FROM PUBLIC,app_login;
GRANT EXECUTE ON FUNCTION auth.issue_challenge(text,text,text,text),auth.email_delivery(uuid,boolean),
 auth.email_session(uuid,text,uuid,text,text) TO app_auth;
SET LOCAL ROLE app_owner;
