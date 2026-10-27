GRANT SELECT ON public.users, public.memberships TO auth_fn_owner;
GRANT SELECT (id,status,deleted_at) ON public.tenants TO auth_fn_owner;
GRANT SELECT,INSERT ON public.sessions TO auth_fn_owner;
GRANT UPDATE(last_seen_at,expires_at,csrf_token_hash,authz_version,version,revoked_at,revoke_reason)
  ON public.sessions TO auth_fn_owner;
GRANT SELECT,INSERT ON public.auth_challenges TO auth_fn_owner;
GRANT UPDATE(csrf_token_hash,revoked_at) ON public.auth_challenges TO auth_fn_owner;
GRANT SELECT,INSERT ON public.auth_rate_limits TO auth_fn_owner;
GRANT UPDATE(counter,blocked_until) ON public.auth_rate_limits TO auth_fn_owner;
GRANT INSERT ON public.auth_events TO auth_fn_owner;
GRANT USAGE ON SEQUENCE public.auth_events_id_seq TO auth_fn_owner;

SET LOCAL ROLE auth_fn_owner;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE FUNCTION auth.credentials(p_email text)
RETURNS TABLE(user_id uuid,password_hash varchar,email varchar)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT id,password_hash,email FROM public.users
 WHERE lower(email)=p_email AND status='ACTIVE' AND deleted_at IS NULL;
$$;
CREATE FUNCTION auth.memberships(p_user uuid)
RETURNS TABLE(membership_id uuid,tenant_id bigint,kind varchar,authz_version bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT m.id,m.tenant_id,m.kind,m.authz_version FROM public.memberships m
 JOIN public.users u ON u.id=m.user_id JOIN public.tenants t ON t.id=m.tenant_id
 WHERE m.user_id=p_user AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND t.status='ACTIVE' AND t.deleted_at IS NULL ORDER BY m.tenant_id,m.id;
$$;
CREATE FUNCTION auth.resolve_session(p_token_hash text)
RETURNS TABLE(session_id uuid,user_id uuid,membership_id uuid,tenant_id bigint,
 display_name varchar,email varchar,member_kind varchar,issued_authz_version bigint,
 current_authz_version bigint,expires_at timestamptz,absolute_expires_at timestamptz,
 session_version integer)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT s.id,s.user_id,s.membership_id,s.tenant_id,u.display_name,u.email,m.kind,
 s.authz_version,m.authz_version,s.expires_at,s.absolute_expires_at,s.version
 FROM public.sessions s JOIN public.users u ON u.id=s.user_id
 JOIN public.memberships m ON (m.tenant_id,m.id,m.user_id)=(s.tenant_id,s.membership_id,s.user_id)
 JOIN public.tenants t ON t.id=s.tenant_id
 WHERE s.token_hash=p_token_hash AND s.revoked_at IS NULL
 AND s.expires_at>statement_timestamp() AND s.absolute_expires_at>statement_timestamp()
 AND u.status='ACTIVE' AND u.deleted_at IS NULL AND m.status='ACTIVE'
 AND t.status='ACTIVE' AND t.deleted_at IS NULL;
$$;
CREATE FUNCTION auth.issue_preauth(p_cookie text,p_csrf text) RETURNS timestamptz
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE expiry timestamptz := statement_timestamp()+interval '10 minutes';
BEGIN
 IF p_cookie !~ '^[0-9a-f]{64}$' OR p_csrf !~ '^[0-9a-f]{64}$' THEN
   RAISE EXCEPTION 'invalid challenge' USING ERRCODE='22023'; END IF;
 INSERT INTO public.auth_challenges(cookie_token_hash,csrf_token_hash,expires_at)
 VALUES(p_cookie,p_csrf,expiry); RETURN expiry;
END; $$;
CREATE FUNCTION auth.resolve_preauth(p_cookie text)
RETURNS TABLE(challenge_id uuid,csrf_token_hash varchar,expires_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT id,csrf_token_hash,expires_at FROM public.auth_challenges
 WHERE cookie_token_hash=p_cookie AND revoked_at IS NULL AND expires_at>statement_timestamp();
$$;
CREATE FUNCTION auth.rate_limit(p_digest text,p_kind text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE start_at timestamptz; attempts integer; span integer; maximum integer;
BEGIN
 IF p_digest !~ '^[0-9a-f]{64}$' OR p_kind NOT IN ('PASSWORD','EMAIL_SEND','EMAIL_VERIFY')
 THEN RAISE EXCEPTION 'invalid rate bucket' USING ERRCODE='22023'; END IF;
 span := CASE WHEN p_kind='EMAIL_SEND' THEN 60 ELSE 300 END;
 maximum := CASE WHEN p_kind='EMAIL_SEND' THEN 1 ELSE 5 END;
 start_at:=to_timestamp(floor(extract(epoch FROM statement_timestamp())/span)*span);
 INSERT INTO public.auth_rate_limits(key_digest,bucket_kind,window_start,counter)
 VALUES(p_digest,p_kind,start_at,1)
 ON CONFLICT(key_digest,bucket_kind,window_start) DO UPDATE
 SET counter=least(public.auth_rate_limits.counter+1,maximum+1)
 RETURNING counter INTO attempts;
 IF attempts>maximum THEN
   RETURN greatest(1,ceil(extract(epoch FROM start_at+make_interval(secs=>span)-statement_timestamp()))::integer);
 END IF;
 RETURN 0;
END; $$;
CREATE FUNCTION auth.record_event(p_email text,p_outcome text,p_request text,p_ip text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF p_outcome NOT IN ('PASSWORD_REJECTED','PASSWORD_RATE_LIMITED')
   OR p_email !~ '^[0-9a-f]{64}$' OR p_ip !~ '^[0-9a-f]{64}$'
   OR length(p_request) NOT BETWEEN 1 AND 64
 THEN RAISE EXCEPTION 'invalid auth event' USING ERRCODE='22023'; END IF;
 INSERT INTO public.auth_events(outcome,email_digest,request_id,ip_digest)
 VALUES(p_outcome,p_email,p_request,p_ip);
END; $$;
CREATE FUNCTION auth.create_session(p_user uuid,p_member uuid,p_token text,p_csrf text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE member public.memberships%ROWTYPE; created uuid; preauth uuid;
BEGIN
 SELECT m.* INTO member FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 JOIN public.tenants t ON t.id=m.tenant_id
 WHERE m.id=p_member AND m.user_id=p_user AND m.status='ACTIVE'
 AND u.status='ACTIVE' AND u.deleted_at IS NULL AND t.status='ACTIVE' AND t.deleted_at IS NULL;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF p_token !~ '^[0-9a-f]{64}$' OR p_csrf !~ '^[0-9a-f]{64}$'
   OR current_setting('app.email_digest',true) !~ '^[0-9a-f]{64}$'
 THEN RAISE EXCEPTION 'invalid session input' USING ERRCODE='22023'; END IF;
 UPDATE public.auth_challenges SET revoked_at=statement_timestamp()
 WHERE cookie_token_hash=current_setting('app.preauth_hash',true)
   AND csrf_token_hash=current_setting('app.preauth_csrf_hash',true)
   AND revoked_at IS NULL AND expires_at>statement_timestamp() RETURNING id INTO preauth;
 IF NOT FOUND THEN RETURN NULL; END IF;
 INSERT INTO public.sessions(token_hash,csrf_token_hash,tenant_id,user_id,membership_id,
   authz_version,expires_at,absolute_expires_at)
 VALUES(p_token,p_csrf,member.tenant_id,p_user,p_member,member.authz_version,
   now()+interval '2 hours',now()+interval '7 days') RETURNING id INTO created;
 INSERT INTO public.auth_events(tenant_id,user_id,outcome,email_digest,request_id,ip_digest)
 VALUES(member.tenant_id,p_user,'PASSWORD_ACCEPTED',current_setting('app.email_digest',true),
   current_setting('app.request_id',true),current_setting('app.ip_digest',true));
 RETURN created;
END; $$;
CREATE FUNCTION auth.renew_session(p_token text,p_version integer) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE updated uuid;
BEGIN
 UPDATE public.sessions s SET last_seen_at=statement_timestamp(),
   expires_at=least(statement_timestamp()+interval '2 hours',s.absolute_expires_at),
   authz_version=current_authz_version,version=s.version+1
 FROM auth.resolve_session(p_token) valid
 WHERE s.id=valid.session_id AND s.version=p_version AND s.revoked_at IS NULL
 RETURNING s.id INTO updated;
 RETURN updated IS NOT NULL;
END; $$;
CREATE FUNCTION auth.rotate_csrf(p_token text,p_csrf text,p_version integer) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE updated uuid;
BEGIN
 IF p_csrf !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid csrf' USING ERRCODE='22023'; END IF;
 UPDATE public.sessions s SET csrf_token_hash=p_csrf,version=s.version+1
 FROM auth.resolve_session(p_token) valid
 WHERE s.id=valid.session_id AND s.version=p_version AND s.revoked_at IS NULL
 RETURNING s.id INTO updated;
 RETURN updated IS NOT NULL;
END; $$;
CREATE FUNCTION auth.revoke_session(p_token text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE revoked public.sessions%ROWTYPE;
BEGIN
 UPDATE public.sessions SET revoked_at=statement_timestamp(),revoke_reason='LOGOUT',version=version+1
 WHERE token_hash=p_token AND revoked_at IS NULL RETURNING * INTO revoked;
 IF FOUND THEN
   INSERT INTO public.auth_events(tenant_id,user_id,outcome,email_digest,request_id,ip_digest)
   VALUES(revoked.tenant_id,revoked.user_id,'LOGOUT',current_setting('app.email_digest',true),
     current_setting('app.request_id',true),current_setting('app.ip_digest',true));
 END IF;
END; $$;

-- 内部最小布尔守卫：不扩展resolve_session输出、不向调用者返回CSRF摘要。
CREATE FUNCTION auth.check_csrf(p_token text,p_csrf text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS (SELECT 1 FROM public.sessions s JOIN auth.resolve_session(p_token) v
 ON v.session_id=s.id WHERE s.csrf_token_hash=p_csrf);
$$;

REVOKE ALL ON FUNCTION auth.credentials(text),auth.memberships(uuid),auth.resolve_session(text),
 auth.issue_preauth(text,text),auth.resolve_preauth(text),auth.rate_limit(text,text),
 auth.record_event(text,text,text,text),auth.create_session(uuid,uuid,text,text),
 auth.renew_session(text,integer),auth.rotate_csrf(text,text,integer),auth.revoke_session(text),
 auth.check_csrf(text,text)
 FROM PUBLIC,app_login;
GRANT EXECUTE ON FUNCTION auth.credentials(text),auth.memberships(uuid),auth.resolve_session(text),
 auth.issue_preauth(text,text),auth.resolve_preauth(text),auth.rate_limit(text,text),
 auth.record_event(text,text,text,text),auth.create_session(uuid,uuid,text,text),
 auth.renew_session(text,integer),auth.rotate_csrf(text,text,integer),auth.revoke_session(text),
 auth.check_csrf(text,text)
 TO app_auth;
SET LOCAL ROLE app_owner;
