CREATE FUNCTION access.analysis_coverage_failed(p_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item public.analysis_tasks;
BEGIN
 SELECT * INTO item FROM public.analysis_tasks WHERE id=p_id
 AND tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id() FOR UPDATE;
 IF NOT FOUND OR NOT access.analysis_visible(item.tenant_id,item.user_id,item.shop_id,
   item.platform_id,item.account_scope) THEN
  RAISE EXCEPTION 'analysis scope denied' USING ERRCODE='42501'; END IF;
 UPDATE public.analysis_tasks SET status='failed',error='DATA_COVERAGE_INSUFFICIENT: 数据覆盖已变化，请补齐后以新请求重新分析'
 WHERE id=p_id AND status='queued';
END $$;
REVOKE ALL ON FUNCTION access.analysis_coverage_failed(uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.analysis_coverage_failed(uuid) TO app_login;
