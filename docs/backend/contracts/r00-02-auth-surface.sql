-- R00-02设计参考SQL，仅展示resolve_session及权限边界；不是已执行迁移。
-- 前置：13册身份表、角色/auth schema；由独立迁移身份显式SET ROLE执行。
begin;
set local role app_owner;
-- 表权限由独立引导/迁移身份在同一发布窗口授予。示例是明确白名单，非全schema授权。
grant select on public.users, public.memberships to auth_fn_owner;
grant select (id, status, deleted_at) on public.tenants to auth_fn_owner;
grant select, insert on public.sessions to auth_fn_owner;
grant update (last_seen_at, expires_at, csrf_token_hash, authz_version, version, revoked_at, revoke_reason)
  on public.sessions to auth_fn_owner;
grant select, insert on public.email_codes to auth_fn_owner;
grant update (attempts, verified_at, consumed_at) on public.email_codes to auth_fn_owner;
grant select, insert on public.auth_challenges to auth_fn_owner;
grant update (csrf_token_hash, revoked_at) on public.auth_challenges to auth_fn_owner;
grant select, insert on public.auth_rate_limits to auth_fn_owner;
grant update (counter, blocked_until) on public.auth_rate_limits to auth_fn_owner;
grant insert on public.auth_events to auth_fn_owner;
grant usage on sequence public.auth_events_id_seq to auth_fn_owner;

revoke all on public.users, public.sessions, public.email_codes, public.auth_challenges,
  public.auth_rate_limits, public.auth_events from app_login, app_auth;


set local role auth_fn_owner;
revoke all on schema auth from public;
grant usage on schema auth to app_auth;

create or replace function auth.resolve_session(p_token_hash text)
returns table (
  session_id uuid, user_id uuid, membership_id uuid, tenant_id bigint,
  display_name varchar, email varchar, member_kind varchar,
  issued_authz_version bigint, current_authz_version bigint,
  expires_at timestamptz, absolute_expires_at timestamptz, session_version integer
)
language sql stable security definer
set search_path = pg_catalog, pg_temp
as $body$
  select s.id, s.user_id, s.membership_id, s.tenant_id,
         u.display_name, u.email, m.kind, s.authz_version, m.authz_version,
         s.expires_at, s.absolute_expires_at, s.version
  from public.sessions as s
  join public.users as u on u.id = s.user_id
  join public.memberships as m
    on (m.tenant_id, m.id, m.user_id) = (s.tenant_id, s.membership_id, s.user_id)
  join public.tenants as t on t.id = s.tenant_id
  where s.token_hash = p_token_hash
    and s.revoked_at is null
    and s.expires_at > pg_catalog.statement_timestamp()
    and s.absolute_expires_at > pg_catalog.statement_timestamp()
    and u.status = 'ACTIVE' and u.deleted_at is null
    and m.status = 'ACTIVE'
    and t.status = 'ACTIVE' and t.deleted_at is null;
$body$;

revoke all on function auth.resolve_session(text) from public, app_login;
grant execute on function auth.resolve_session(text) to app_auth;
alter default privileges for role auth_fn_owner revoke execute on functions from public;
commit;

-- 其余函数仅在database-auth-contract.json中冻结接口和守卫，M0按实际函数精确签名授权。
-- 不预先GRANT EXECUTE ON ALL FUNCTIONS，也不创建返回假成功的函数占位实现。
