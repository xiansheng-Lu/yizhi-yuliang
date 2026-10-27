CREATE FUNCTION access.job_approval_current(p_member uuid,p_user uuid,p_job uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item jsonb; reviewer uuid; tenant bigint:=public.current_tenant_id();
BEGIN
 -- 调用者先通过精确任务/申请人/CLAIM权限核验；不能借本函数探测任意用户。
 item:=access.job_state_context(p_member,p_user,p_job,'CLAIM');
 IF item->'document'->'approval'->>'state'<>'APPROVED'
  OR item->'document'->'approval'->>'method'<>'MANUAL' THEN RETURN false; END IF;
 BEGIN reviewer:=(item->'document'->'approval'->>'actor_id')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN RETURN false; END;
 IF reviewer IS NULL OR reviewer=p_user THEN RETURN false; END IF;
 RETURN EXISTS(
  SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  WHERE m.tenant_id=tenant AND m.user_id=reviewer AND m.status='ACTIVE'
   AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
    WHERE g.tenant_id=tenant AND g.membership_id=m.id AND rp.permission_code='task.approve')
   AND EXISTS(SELECT 1 FROM public.shops s JOIN public.entity_links l
    ON (l.tenant_id,l.shop_id)=(s.tenant_id,s.id)
    JOIN public.ad_accounts a ON (a.tenant_id,a.id)=(l.tenant_id,l.account_id)
    WHERE s.tenant_id=tenant AND s.id=(item->>'shop_id')::uuid
     AND a.id=(item->>'account_id')::uuid AND s.deleted_at IS NULL AND a.deleted_at IS NULL
     AND a.status IN ('BOUND','AUTHORIZED','DEGRADED'))
   AND EXISTS(SELECT 1 FROM public.shops s JOIN public.data_scopes ds ON ds.tenant_id=s.tenant_id
    WHERE s.tenant_id=tenant AND s.id=(item->>'shop_id')::uuid AND ds.membership_id=m.id
     AND (ds.permissions_mask IS NULL OR 'task.approve'=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id))
   AND EXISTS(SELECT 1 FROM public.ad_accounts a JOIN public.data_scopes ds ON ds.tenant_id=a.tenant_id
    WHERE a.tenant_id=tenant AND a.id=(item->>'account_id')::uuid AND ds.membership_id=m.id
     AND (ds.permissions_mask IS NULL OR 'task.approve'=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.account_id=a.id OR ds.entity_id=a.entity_id))
 );
END $$;
REVOKE ALL ON FUNCTION access.job_approval_current(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.job_approval_current(uuid,uuid,uuid) TO app_login;
