CREATE TABLE analysis_executions (
 tenant_id bigint NOT NULL,task_id uuid NOT NULL,token uuid NOT NULL,
 lease_until timestamptz NOT NULL,snapshot jsonb,result_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,task_id),
 FOREIGN KEY(tenant_id,task_id) REFERENCES analysis_tasks(tenant_id,id)
);
ALTER TABLE analysis_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_executions FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON analysis_executions TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY own_read ON analysis_executions FOR SELECT TO app_login USING(
 EXISTS(SELECT 1 FROM analysis_tasks t WHERE (t.tenant_id,t.id)=(analysis_executions.tenant_id,task_id)));
GRANT SELECT ON analysis_executions TO app_login;
CREATE FUNCTION access.analysis_execute(p_id uuid,p_token uuid,p_stage integer,p_result jsonb DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item public.analysis_tasks; ex public.analysis_executions; fresh uuid:=gen_random_uuid();
BEGIN
 SELECT * INTO item FROM public.analysis_tasks WHERE id=p_id
 AND tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id() FOR UPDATE;
 IF NOT FOUND OR NOT access.analysis_visible(item.tenant_id,item.user_id,item.shop_id,
 item.platform_id,item.account_scope) THEN
 RAISE EXCEPTION 'analysis scope denied' USING ERRCODE='42501'; END IF;
 IF item.status IN ('succeeded','failed') THEN RETURN NULL; END IF;
 SELECT * INTO ex FROM public.analysis_executions WHERE task_id=p_id AND tenant_id=item.tenant_id;
 IF p_stage=10 THEN
  IF FOUND AND ex.lease_until>clock_timestamp() THEN RETURN NULL; END IF;
  INSERT INTO public.analysis_executions(tenant_id,task_id,token,lease_until,result_id)
  VALUES(item.tenant_id,p_id,fresh,clock_timestamp()+interval '120 seconds',gen_random_uuid())
  ON CONFLICT(tenant_id,task_id) DO UPDATE SET token=fresh,lease_until=excluded.lease_until;
  UPDATE public.analysis_tasks SET status='running',progress=10 WHERE id=p_id;
  RETURN fresh;
 END IF;
 IF NOT FOUND OR p_token IS NULL OR ex.token IS DISTINCT FROM p_token
 OR ex.lease_until IS NULL OR ex.lease_until<=clock_timestamp() THEN
 RAISE EXCEPTION 'analysis stale fence' USING ERRCODE='P0409'; END IF;
 IF p_stage=40 AND item.progress=10 THEN
  UPDATE public.analysis_executions SET snapshot=coalesce(snapshot,(SELECT coalesce(jsonb_agg(to_jsonb(v)),'[]'::jsonb)
   FROM (SELECT f.id,f.fact_date,f.product_line,f.account_id,f.metric_name,f.unit,f.currency,
     f.attribution_window,f.refund_policy,f.value_num::text,f.revision_number,
     f.sync_run_id,f.page_no,f.outbound_id
     FROM public.metric_facts f WHERE f.tenant_id=item.tenant_id AND f.shop_id=item.shop_id
     AND f.account_id=ANY(item.account_scope)
     AND f.account_id=ANY(access.analysis_accounts(item.shop_id,item.platform_id))
     AND f.granularity='DAY' AND f.entity_kind='ACCOUNT'
     AND f.fact_date BETWEEN (item.query_json->>'startDate')::date AND (item.query_json->>'endDate')::date
     AND ((item.query_json->'dimensions' ? 'ads' AND f.product_line='adq'
       AND f.metric_name IN ('spend_minor','paid_gmv_minor','impressions','clicks'))
      OR (item.query_json->'dimensions' ? 'shop' AND f.product_line='wxshop'
       AND f.metric_name IN ('paid_gmv_minor','paid_order_count','refund_gmv_minor')))) v))
   WHERE task_id=p_id AND tenant_id=item.tenant_id;
 ELSIF p_stage=75 AND item.progress=40 THEN NULL;
 ELSIF p_stage=90 AND item.progress=75 THEN NULL;
 ELSIF p_stage=100 AND item.progress=90 THEN
  IF (p_result->>'id'=ex.result_id::text AND p_result->>'taskId'=p_id::text
      AND p_result->>'schemaVersion'='business-analysis-v1' AND p_result->>'source'='API'
      AND p_result->'query'=item.query_json AND jsonb_typeof(p_result->'metrics')='array'
      AND jsonb_typeof(p_result->'points')='array') IS NOT TRUE THEN
   RAISE EXCEPTION 'analysis result invalid' USING ERRCODE='22023'; END IF;
  INSERT INTO public.analysis_results(id,tenant_id,user_id,task_id,payload)
  VALUES(ex.result_id,item.tenant_id,item.user_id,p_id,p_result);
  UPDATE public.analysis_tasks SET status='succeeded',progress=100,result_id=ex.result_id
  WHERE id=p_id;
  RETURN p_token;
 ELSIF p_stage=-1 THEN
  UPDATE public.analysis_tasks SET status='failed',error='ANALYSIS_COMPUTATION_FAILED: 请核对事实口径后以新请求重新分析'
  WHERE id=p_id;
  RETURN p_token;
 ELSE RAISE EXCEPTION 'analysis stage invalid' USING ERRCODE='22023'; END IF;
 UPDATE public.analysis_tasks SET progress=p_stage WHERE id=p_id;
 UPDATE public.analysis_executions SET lease_until=clock_timestamp()+interval '120 seconds'
 WHERE task_id=p_id AND tenant_id=item.tenant_id;
 RETURN p_token;
END $$;
REVOKE ALL ON FUNCTION access.analysis_execute(uuid,uuid,integer,jsonb) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.analysis_execute(uuid,uuid,integer,jsonb) TO app_login;
