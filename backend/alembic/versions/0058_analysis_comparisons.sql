ALTER TABLE analysis_executions ADD COLUMN comparisons_snapshot jsonb;
ALTER TABLE analysis_executions ADD CONSTRAINT comparison_snapshot_object
 CHECK(comparisons_snapshot IS NULL OR jsonb_typeof(comparisons_snapshot)='object');
CREATE FUNCTION access.analysis_fact_snapshot(p_id uuid,p_start date,p_end date) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT coalesce(jsonb_agg(to_jsonb(v)),'[]'::jsonb) FROM (
 SELECT f.id,f.fact_date,f.product_line,f.account_id,f.metric_name,f.unit,f.currency,
 f.attribution_window,f.refund_policy,f.value_num::text,f.revision_number,
 f.sync_run_id,f.page_no,f.outbound_id FROM public.analysis_tasks t JOIN public.metric_facts f
 ON f.tenant_id=t.tenant_id AND f.shop_id=t.shop_id AND f.account_id=ANY(t.account_scope)
 WHERE t.id=p_id AND f.granularity='DAY' AND f.entity_kind='ACCOUNT'
 AND f.fact_date BETWEEN p_start AND p_end
 AND ((t.query_json->'dimensions' ? 'ads' AND f.product_line='adq'
 AND f.metric_name IN ('spend_minor','paid_gmv_minor','impressions','clicks'))
 OR (t.query_json->'dimensions' ? 'shop' AND f.product_line='wxshop'
 AND f.metric_name IN ('paid_gmv_minor','paid_order_count','refund_gmv_minor')))
 ORDER BY f.fact_date,f.account_id,f.metric_name,f.id) v
$$;
REVOKE ALL ON FUNCTION access.analysis_fact_snapshot(uuid,date,date) FROM PUBLIC,app_login,app_auth;
CREATE FUNCTION access.analysis_comparisons_freeze(p_id uuid,p_token uuid,p_meta jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE task public.analysis_tasks; ex public.analysis_executions;
 start_day date; end_day date; n integer; year_start date; year_end date; month_end date;
 prior_start date; prior_end date; part text; first_day date; last_day date; status text;
 bundle jsonb:='{}'::jsonb; entry jsonb; facts jsonb;
BEGIN
 SELECT * INTO task FROM public.analysis_tasks WHERE id=p_id AND tenant_id=public.current_tenant_id()
 AND user_id=public.current_actor_id() FOR UPDATE;
 IF NOT FOUND OR NOT access.analysis_visible(task.tenant_id,task.user_id,task.shop_id,task.platform_id,task.account_scope)
 THEN RAISE EXCEPTION 'analysis scope denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO ex FROM public.analysis_executions WHERE task_id=p_id AND tenant_id=task.tenant_id FOR UPDATE;
 IF NOT FOUND OR p_token IS NULL OR ex.token IS DISTINCT FROM p_token OR ex.lease_until IS NULL
 OR ex.lease_until<=clock_timestamp() OR task.status<>'running' OR task.progress<>10 THEN
 RAISE EXCEPTION 'analysis stale fence' USING ERRCODE='P0409'; END IF;

 start_day:=(task.query_json->>'startDate')::date;end_day:=(task.query_json->>'endDate')::date;
 n:=end_day-start_day+1;prior_start:=start_day-n;prior_end:=start_day-1;
 month_end:=(make_date(extract(year FROM start_day)::int-1,extract(month FROM start_day)::int,1)
  +interval '1 month'-interval '1 day')::date;
 year_start:=make_date(extract(year FROM start_day)::int-1,extract(month FROM start_day)::int,
  least(extract(day FROM start_day)::int,extract(day FROM month_end)::int));
 IF extract(day FROM start_day)=1 AND date_trunc('month',start_day)=date_trunc('month',end_day)
 AND end_day=(date_trunc('month',start_day)+interval '1 month'-interval '1 day')::date
 THEN year_end:=month_end;ELSE year_end:=year_start+n-1;END IF;
 IF ex.snapshot IS NOT NULL THEN
  IF ex.comparisons_snapshot IS NOT NULL THEN RETURN;END IF;
  p_meta:=jsonb_build_object('previous',jsonb_build_object(
   'status','NOT_CAPTURED_LEGACY','reason','旧在途快照仅冻结本期，未记录当时比较期；需新任务重新分析',
   'startDate',prior_start,'endDate',prior_end),
   'yearAgo',jsonb_build_object('status','NOT_CAPTURED_LEGACY',
   'reason','旧在途快照仅冻结本期，未记录当时比较期；需新任务重新分析',
   'startDate',year_start,'endDate',year_end));
 END IF;
 IF jsonb_typeof(p_meta) IS DISTINCT FROM 'object' OR NOT(p_meta ?& ARRAY['previous','yearAgo'])
 OR p_meta-ARRAY['previous','yearAgo']<>'{}'::jsonb THEN
 RAISE EXCEPTION 'comparison metadata invalid' USING ERRCODE='22023';END IF;
 FOREACH part IN ARRAY ARRAY['previous','yearAgo'] LOOP
  IF part='previous' THEN first_day:=prior_start;last_day:=prior_end;
  ELSE first_day:=year_start;last_day:=year_end;END IF;
  entry:=p_meta->part;status:=entry->>'status';
  IF status IS NULL OR status NOT IN ('complete','missing','incomparable','NOT_CAPTURED_LEGACY')
   OR (status='NOT_CAPTURED_LEGACY' AND ex.snapshot IS NULL)
   OR entry->>'startDate' IS DISTINCT FROM to_char(first_day,'YYYY-MM-DD')
   OR entry->>'endDate' IS DISTINCT FROM to_char(last_day,'YYYY-MM-DD') THEN
   RAISE EXCEPTION 'comparison window invalid' USING ERRCODE='22023';END IF;
  IF status='complete' THEN facts:=access.analysis_fact_snapshot(p_id,first_day,last_day);
  ELSE facts:='[]'::jsonb;END IF;
  bundle:=bundle||jsonb_build_object(part,entry||jsonb_build_object('facts',facts));
 END LOOP;
 UPDATE public.analysis_executions SET snapshot=coalesce(snapshot,access.analysis_fact_snapshot(p_id,start_day,end_day)),
 comparisons_snapshot=bundle WHERE task_id=p_id AND tenant_id=task.tenant_id;
END $$;
REVOKE ALL ON FUNCTION access.analysis_comparisons_freeze(uuid,uuid,jsonb) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.analysis_comparisons_freeze(uuid,uuid,jsonb) TO app_login;
CREATE OR REPLACE FUNCTION access.analysis_execute(p_id uuid,p_token uuid,p_stage integer,p_result jsonb DEFAULT NULL)
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
  IF ex.snapshot IS NULL OR ex.comparisons_snapshot IS NULL THEN
   RAISE EXCEPTION 'analysis snapshot not frozen' USING ERRCODE='22023'; END IF;
  UPDATE public.analysis_executions SET snapshot=coalesce(snapshot,(SELECT coalesce(jsonb_agg(to_jsonb(v)),'[]'::jsonb)
   FROM (SELECT f.id,f.fact_date,f.product_line,f.account_id,f.metric_name,f.unit,f.currency,
     f.attribution_window,f.refund_policy,f.value_num::text,f.revision_number,
     f.sync_run_id,f.page_no,f.outbound_id
     FROM public.metric_facts f WHERE f.tenant_id=item.tenant_id AND f.shop_id=item.shop_id
     AND f.account_id=ANY(item.account_scope)

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
