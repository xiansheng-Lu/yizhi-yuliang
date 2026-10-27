REVOKE ALL ON SCHEMA access FROM PUBLIC;
GRANT USAGE ON SCHEMA access TO app_login;

CREATE TABLE access_change_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id bigint NOT NULL REFERENCES tenants(id),
  actor_membership_id uuid NOT NULL,
  target_membership_id uuid NOT NULL,
  action varchar(40) NOT NULL,
  old_value jsonb NOT NULL,
  new_value jsonb NOT NULL,
  request_id varchar(64) NOT NULL CHECK (length(request_id) BETWEEN 1 AND 64),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id,actor_membership_id) REFERENCES memberships(tenant_id,id),
  FOREIGN KEY (tenant_id,target_membership_id) REFERENCES memberships(tenant_id,id)
);
ALTER TABLE access_change_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE access_change_events FORCE ROW LEVEL SECURITY;
CREATE POLICY access_events_owner ON access_change_events FOR ALL TO app_owner USING (true) WITH CHECK (true);
CREATE POLICY access_events_read ON access_change_events FOR SELECT TO app_login
  USING (tenant_id=public.current_tenant_id());
GRANT SELECT ON access_change_events TO app_login;

CREATE FUNCTION access.seed_tenant_roles(p_tenant bigint) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE template record; role_uuid uuid;
BEGIN
  FOR template IN SELECT code,name FROM public.role_templates LOOP
    role_uuid := NULL;
    INSERT INTO public.roles(tenant_id,code,template_code,name)
      VALUES (p_tenant,template.code,template.code,template.name)
      ON CONFLICT (tenant_id,code) DO NOTHING RETURNING id INTO role_uuid;
    IF role_uuid IS NOT NULL THEN
      INSERT INTO public.role_permissions(tenant_id,role_id,permission_code)
        SELECT p_tenant,role_uuid,permission_code FROM public.role_template_permissions
        WHERE template_code=template.code;
    END IF;
  END LOOP;
END; $$;
REVOKE ALL ON FUNCTION access.seed_tenant_roles(bigint) FROM PUBLIC,app_login,app_auth;

CREATE FUNCTION access.change_member_scope(
  p_actor_session_hash text, p_target uuid, p_expected_version bigint,
  p_status text, p_scopes jsonb, p_request_id text
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor public.sessions%ROWTYPE; target public.memberships%ROWTYPE;
        entry jsonb; previous jsonb; next_version bigint;
BEGIN
  SELECT s.* INTO actor FROM public.sessions s
    JOIN public.users u ON u.id=s.user_id
    JOIN public.tenants t ON t.id=s.tenant_id
    JOIN public.memberships m ON (m.tenant_id,m.id,m.user_id)=(s.tenant_id,s.membership_id,s.user_id)
    WHERE s.token_hash=p_actor_session_hash AND s.revoked_at IS NULL
      AND s.expires_at>statement_timestamp() AND s.absolute_expires_at>statement_timestamp()
      AND u.status='ACTIVE' AND u.deleted_at IS NULL
      AND t.status='ACTIVE' AND t.deleted_at IS NULL AND m.status='ACTIVE'
      AND s.tenant_id=public.current_tenant_id();
  IF NOT FOUND OR NOT EXISTS (
    SELECT 1 FROM public.role_grants g JOIN public.role_permissions p
      ON (p.tenant_id,p.role_id)=(g.tenant_id,g.role_id)
      WHERE g.tenant_id=actor.tenant_id AND g.membership_id=actor.membership_id
        AND p.permission_code='access.manage'
  ) THEN RAISE EXCEPTION 'access denied' USING ERRCODE='42501'; END IF;
  SELECT * INTO target FROM public.memberships
    WHERE tenant_id=actor.tenant_id AND id=p_target FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'access denied' USING ERRCODE='42501'; END IF;
  IF target.authz_version<>p_expected_version THEN
    RAISE EXCEPTION 'version conflict' USING ERRCODE='40001';
  END IF;
  IF p_status IS NULL OR p_status NOT IN ('ACTIVE','REMOVED')
    OR p_scopes IS NULL OR jsonb_typeof(p_scopes)<>'array' OR jsonb_array_length(p_scopes)>100
    OR p_request_id IS NULL OR length(p_request_id) NOT BETWEEN 1 AND 64
  THEN RAISE EXCEPTION 'invalid access change' USING ERRCODE='22023'; END IF;
  SELECT jsonb_build_object('status',target.status,'authzVersion',target.authz_version,
    'scopes',coalesce(jsonb_agg(to_jsonb(s)),'[]'::jsonb)) INTO previous
    FROM public.data_scopes s WHERE s.tenant_id=actor.tenant_id AND s.membership_id=p_target;
  DELETE FROM public.data_scopes WHERE tenant_id=actor.tenant_id AND membership_id=p_target;
  FOR entry IN SELECT * FROM jsonb_array_elements(p_scopes) LOOP
    IF jsonb_typeof(entry)<>'object' OR (entry - ARRAY['scopeType','entityId','shopId','accountId','permissions'])<>'{}'::jsonb
    THEN RAISE EXCEPTION 'invalid scope fields' USING ERRCODE='22023'; END IF;
    INSERT INTO public.data_scopes(tenant_id,membership_id,scope_type,entity_id,shop_id,account_id,permissions_mask)
      VALUES (actor.tenant_id,p_target,entry->>'scopeType',(entry->>'entityId')::uuid,
        (entry->>'shopId')::uuid,(entry->>'accountId')::uuid,
        CASE WHEN entry ? 'permissions' THEN ARRAY(SELECT jsonb_array_elements_text(entry->'permissions')) ELSE NULL END);
  END LOOP;
  UPDATE public.memberships SET status=p_status,authz_version=authz_version+1
    WHERE tenant_id=actor.tenant_id AND id=p_target RETURNING authz_version INTO next_version;
  INSERT INTO public.access_change_events(tenant_id,actor_membership_id,target_membership_id,action,
      old_value,new_value,request_id)
    VALUES(actor.tenant_id,actor.membership_id,p_target,'MEMBER_SCOPE_CHANGED',previous,
      jsonb_build_object('status',p_status,'authzVersion',next_version,'scopes',p_scopes),p_request_id);
  RETURN next_version;
END; $$;
REVOKE ALL ON FUNCTION access.change_member_scope(text,uuid,bigint,text,jsonb,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.change_member_scope(text,uuid,bigint,text,jsonb,text) TO app_login;
