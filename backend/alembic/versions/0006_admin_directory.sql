CREATE FUNCTION access.admin_directory(p_token text,p_request_id text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor public.sessions%ROWTYPE; result jsonb;
BEGIN
 SELECT s.* INTO actor FROM public.sessions s JOIN public.users u ON u.id=s.user_id
 JOIN public.tenants t ON t.id=s.tenant_id JOIN public.memberships m
 ON (m.tenant_id,m.id,m.user_id)=(s.tenant_id,s.membership_id,s.user_id)
 WHERE s.token_hash=p_token AND s.revoked_at IS NULL AND s.expires_at>statement_timestamp()
 AND s.absolute_expires_at>statement_timestamp() AND s.tenant_id=public.current_tenant_id()
 AND u.status='ACTIVE' AND u.deleted_at IS NULL AND m.status='ACTIVE' AND m.kind='INTERNAL'
 AND t.status='ACTIVE' AND t.deleted_at IS NULL AND EXISTS (
   SELECT 1 FROM public.role_grants g JOIN public.roles r ON (r.tenant_id,r.id)=(g.tenant_id,g.role_id)
   JOIN public.role_permissions p ON (p.tenant_id,p.role_id)=(r.tenant_id,r.id)
   WHERE g.tenant_id=m.tenant_id AND g.membership_id=m.id
     AND r.code='PLATFORM_ADMIN' AND p.permission_code='admin.access'
 );
 IF NOT FOUND THEN RAISE EXCEPTION 'admin access denied' USING ERRCODE='42501'; END IF;
 IF p_request_id IS NULL OR length(p_request_id) NOT BETWEEN 1 AND 64 THEN
   RAISE EXCEPTION 'invalid request id' USING ERRCODE='22023'; END IF;
 SELECT jsonb_build_object(
   'users',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',u.id::text,
     'displayName',u.display_name,'email',u.email,'status',u.status)),'[]'::jsonb)
     FROM (SELECT id,display_name,email,status FROM public.users ORDER BY id LIMIT 100) u),
   'tenants',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',t.id::text,
     'name',t.name,'status',t.status)),'[]'::jsonb)
     FROM (SELECT id,name,status FROM public.tenants ORDER BY id LIMIT 100) t),
   'limit',100
 ) INTO result;
 INSERT INTO public.access_change_events(tenant_id,actor_membership_id,target_membership_id,
   action,old_value,new_value,request_id)
 VALUES(actor.tenant_id,actor.membership_id,actor.membership_id,'ADMIN_DIRECTORY_READ','{}',
   jsonb_build_object('usersCount',jsonb_array_length(result->'users'),
                     'tenantsCount',jsonb_array_length(result->'tenants')),p_request_id);
 RETURN result;
END; $$;
REVOKE ALL ON FUNCTION access.admin_directory(text,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.admin_directory(text,text) TO app_login;
