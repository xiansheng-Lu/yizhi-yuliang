CREATE TABLE audit_logs (
 id bigint GENERATED ALWAYS AS IDENTITY,
 tenant_id bigint NOT NULL REFERENCES tenants(id),
 actor_id uuid NOT NULL REFERENCES users(id),
 action varchar(60) NOT NULL CHECK (action IN (
   'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change')),
 object_type varchar(30) NOT NULL CHECK (object_type IN ('user','shop','member','role')),
 object_id varchar(64) NOT NULL CHECK (object_id::uuid IS NOT NULL),
 before_hash varchar(64) CHECK (before_hash ~ '^[0-9a-f]{64}$'),
 after_hash varchar(64) CHECK (after_hash ~ '^[0-9a-f]{64}$'),
 request_id uuid NOT NULL,
 ip_inet inet,
 detail jsonb NOT NULL DEFAULT '{"changed_fields":[],"result":"APPLIED"}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(id,created_at),
 CHECK (jsonb_typeof(detail)='object'
   AND detail ?& ARRAY['changed_fields','result']
   AND (detail - ARRAY['changed_fields','result'])='{}'::jsonb
   AND jsonb_typeof(detail->'changed_fields')='array'
   AND jsonb_array_length(detail->'changed_fields')<=8
   AND (detail - 'result') <@ '{"changed_fields":["displayName","name","status","permissions","scope"]}'::jsonb
   AND jsonb_typeof(detail->'result')='string'
   AND detail->>'result' IN ('APPLIED','DENIED'))
) PARTITION BY RANGE(created_at);
CREATE INDEX ix_audit_request ON audit_logs(tenant_id,request_id,created_at);
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_owner ON audit_logs TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY audit_select ON audit_logs FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id());
CREATE POLICY audit_insert ON audit_logs FOR INSERT TO app_login
 WITH CHECK(tenant_id=public.current_tenant_id() AND EXISTS (
  SELECT 1 FROM public.memberships m WHERE m.tenant_id=audit_logs.tenant_id
   AND m.user_id=audit_logs.actor_id AND m.status='ACTIVE'));
REVOKE ALL ON audit_logs FROM PUBLIC,app_login,app_auth;
GRANT SELECT,INSERT ON audit_logs TO app_login;
GRANT USAGE ON SEQUENCE audit_logs_id_seq TO app_login;

-- 仅迁移/维护角色SET ROLE app_owner后执行，绝不SECURITY DEFINER开放运行角色DDL。
CREATE FUNCTION access.ensure_audit_partition(p_month date) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE part text; start_at timestamptz; end_at timestamptz;
BEGIN
 IF p_month IS NULL OR p_month<>date_trunc('month',p_month)::date THEN
   RAISE EXCEPTION 'month boundary required' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(760017);
 part:='audit_logs_'||to_char(p_month,'YYYYMM');
 start_at:=p_month::timestamp AT TIME ZONE 'UTC';
 end_at:=(p_month+interval '1 month')::timestamp AT TIME ZONE 'UTC';
 EXECUTE format('CREATE TABLE IF NOT EXISTS public.%I PARTITION OF public.audit_logs FOR VALUES FROM (%L) TO (%L)',
  part,start_at,end_at);
 EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',part);
 EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',part);
 EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,app_login,app_auth',part);
 EXECUTE format('DROP POLICY IF EXISTS audit_owner ON public.%I',part);
 EXECUTE format('CREATE POLICY audit_owner ON public.%I TO app_owner USING(true) WITH CHECK(true)',part);
END; $$;
REVOKE ALL ON FUNCTION access.ensure_audit_partition(date) FROM PUBLIC,app_login,app_auth;
SELECT access.ensure_audit_partition(date_trunc('month',now() AT TIME ZONE 'UTC')::date);
SELECT access.ensure_audit_partition((date_trunc('month',now() AT TIME ZONE 'UTC')+interval '1 month')::date);

-- 同一业务事务先锁本人资料，保证审计before摘要对应实际更新前状态。
CREATE FUNCTION access.profile_for_update(p_token text) RETURNS varchar
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT u.display_name FROM public.users u
 JOIN public.sessions s ON s.user_id=u.id
 JOIN public.memberships m ON (m.tenant_id,m.id,m.user_id)=(s.tenant_id,s.membership_id,s.user_id)
 JOIN public.tenants t ON t.id=s.tenant_id
 WHERE s.token_hash=p_token AND s.revoked_at IS NULL
  AND s.expires_at>statement_timestamp() AND s.absolute_expires_at>statement_timestamp()
  AND s.tenant_id=public.current_tenant_id() AND m.status='ACTIVE'
  AND u.status='ACTIVE' AND u.deleted_at IS NULL AND t.status='ACTIVE' AND t.deleted_at IS NULL
 FOR UPDATE OF u;
$$;
REVOKE ALL ON FUNCTION access.profile_for_update(text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.profile_for_update(text) TO app_login;
