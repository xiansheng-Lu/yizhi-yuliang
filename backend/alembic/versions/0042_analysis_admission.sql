ALTER TABLE outbox_events DROP CONSTRAINT outbox_events_event_type_check;
ALTER TABLE outbox_events ADD CONSTRAINT outbox_events_event_type_check CHECK(event_type IN (
 'yuliang.system.identity_probe','yuliang.system.schedule_recovery','yuliang.system.execution_recovery',
 'yuliang.system.analysis_accept','yuliang.execute.guard','yuliang.platform.guard','yuliang.ai_media.guard'));

CREATE FUNCTION access.analysis_shop(p_shop uuid,p_permission text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.tenants t ON t.id=m.tenant_id JOIN public.shops s ON s.tenant_id=m.tenant_id
  WHERE m.tenant_id=public.current_tenant_id() AND m.user_id=public.current_actor_id()
   AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND t.status='ACTIVE' AND t.deleted_at IS NULL AND s.id=p_shop AND s.deleted_at IS NULL
   AND s.status IN ('BOUND','AUTHORIZED','DEGRADED')
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
    WHERE g.membership_id=m.id AND rp.permission_code=p_permission)
   AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.membership_id=m.id
    AND (ds.permissions_mask IS NULL OR p_permission=ANY(ds.permissions_mask))
    AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id)))
$$;
CREATE FUNCTION access.analysis_accounts(p_shop uuid,p_platform text) RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT coalesce(array_agg(a.id ORDER BY a.id),'{}'::uuid[]) FROM public.ad_accounts a
 JOIN public.entity_links link ON (link.tenant_id,link.account_id)=(a.tenant_id,a.id)
 WHERE a.tenant_id=public.current_tenant_id() AND link.shop_id=p_shop
  AND access.analysis_shop(p_shop,'overview.read') AND a.deleted_at IS NULL
  AND a.status IN ('BOUND','AUTHORIZED','DEGRADED')
  AND (p_platform='all' OR a.provider||'/'||a.product_line=CASE p_platform
   WHEN 'adq' THEN 'tencent/adq' WHEN 'wxpromo' THEN 'tencent/shop'
   WHEN 'beans' THEN 'beans/beans' WHEN 'wxshop' THEN 'wxshop/wxshop'
   WHEN 'qianchuan' THEN 'ocean/qianchuan' WHEN 'douyinshop' THEN 'ocean/douyinshop'
   WHEN 'juguang' THEN 'xhs/juguang' WHEN 'redshop' THEN 'xhs/redshop' ELSE '' END)
  AND EXISTS(SELECT 1 FROM public.memberships m JOIN public.data_scopes ds
   ON (ds.tenant_id,ds.membership_id)=(m.tenant_id,m.id)
   WHERE m.user_id=public.current_actor_id() AND m.tenant_id=a.tenant_id AND m.status='ACTIVE'
    AND (ds.permissions_mask IS NULL OR 'overview.read'=ANY(ds.permissions_mask))
    AND (ds.scope_type='ALL_TENANT' OR ds.account_id=a.id OR ds.entity_id=a.entity_id))
$$;
CREATE FUNCTION access.analysis_context(p_shop uuid,p_platform text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT access.analysis_shop(p_shop,'overview.read') AND p_platform IN
  ('all','adq','wxpromo','beans','wxshop','qianchuan','douyinshop','juguang','redshop')
 AND (p_platform='all' OR EXISTS(SELECT 1 FROM public.shops s
  WHERE s.tenant_id=public.current_tenant_id() AND s.id=p_shop AND s.platform=p_platform)
  OR cardinality(access.analysis_accounts(p_shop,p_platform))>0)
$$;
CREATE FUNCTION access.analysis_visible(p_tenant bigint,p_user uuid,p_shop uuid,p_platform text,
 p_accounts uuid[]) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,pg_temp AS $$
 SELECT p_tenant=public.current_tenant_id() AND p_user=public.current_actor_id()
  AND access.analysis_context(p_shop,p_platform)
  AND p_accounts<@access.analysis_accounts(p_shop,p_platform)
$$;
CREATE TABLE analysis_tasks (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,user_id uuid NOT NULL,membership_id uuid NOT NULL,
 shop_id uuid NOT NULL,platform_id varchar(20) NOT NULL,account_scope uuid[] NOT NULL,
 query_json jsonb NOT NULL,query_digest varchar(64) NOT NULL,
 idempotency_key varchar(128) NOT NULL,transport_event_id uuid NOT NULL UNIQUE,
 status varchar(10) NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed')),
 progress integer NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
 result_id uuid,error varchar(300),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,user_id,id),UNIQUE(tenant_id,id),UNIQUE(tenant_id,user_id,idempotency_key),
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,shop_id) REFERENCES shops(tenant_id,id),
 FOREIGN KEY(tenant_id,transport_event_id) REFERENCES outbox_events(tenant_id,event_id),
 CHECK(((status='queued' AND progress=0 AND result_id IS NULL AND error IS NULL)
  OR (status='running' AND progress<100 AND result_id IS NULL AND error IS NULL)
  OR (status='succeeded' AND progress=100 AND result_id IS NOT NULL AND error IS NULL)
  OR (status='failed' AND progress<100 AND result_id IS NULL AND length(error)>0)) IS TRUE),
 CHECK((query_json->>'shopId'=shop_id::text AND query_json->>'platformId'=platform_id) IS TRUE)
);
CREATE TABLE analysis_results (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,user_id uuid NOT NULL,task_id uuid NOT NULL UNIQUE,
 payload jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,user_id,task_id,id),
 FOREIGN KEY(tenant_id,user_id,task_id) REFERENCES analysis_tasks(tenant_id,user_id,id),
 CHECK((jsonb_typeof(payload)='object' AND payload->>'schemaVersion'='business-analysis-v1'
  AND payload->>'source'='API' AND payload->>'id'=id::text AND payload->>'taskId'=task_id::text) IS TRUE)
);
ALTER TABLE analysis_tasks ADD CONSTRAINT fk_analysis_result
 FOREIGN KEY(tenant_id,user_id,id,result_id) REFERENCES analysis_results(tenant_id,user_id,task_id,id);
CREATE TABLE analysis_deliveries (
 tenant_id bigint NOT NULL,task_id uuid NOT NULL,event_id uuid NOT NULL UNIQUE,
 received_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(tenant_id,task_id),
 FOREIGN KEY(tenant_id,task_id) REFERENCES analysis_tasks(tenant_id,id),
 FOREIGN KEY(tenant_id,event_id) REFERENCES outbox_events(tenant_id,event_id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['analysis_tasks','analysis_results','analysis_deliveries'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE POLICY own_analysis ON analysis_tasks FOR SELECT TO app_login USING(
 access.analysis_visible(tenant_id,user_id,shop_id,platform_id,account_scope));
CREATE POLICY own_result ON analysis_results FOR SELECT TO app_login USING(
 EXISTS(SELECT 1 FROM analysis_tasks t WHERE (t.tenant_id,t.id)=(analysis_results.tenant_id,task_id)
  AND t.status='succeeded' AND t.result_id=analysis_results.id));
CREATE POLICY own_delivery ON analysis_deliveries FOR SELECT TO app_login USING(
 EXISTS(SELECT 1 FROM analysis_tasks t WHERE (t.tenant_id,t.id)=(analysis_deliveries.tenant_id,task_id)));
CREATE INDEX ix_analysis_history ON analysis_tasks(tenant_id,user_id,created_at DESC,id);
CREATE FUNCTION public.analysis_context_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 IF to_jsonb(NEW)-ARRAY['status','progress','result_id','error']
  <>to_jsonb(OLD)-ARRAY['status','progress','result_id','error'] THEN
  RAISE EXCEPTION 'analysis context is immutable' USING ERRCODE='22023'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER analysis_context_immutable BEFORE UPDATE ON analysis_tasks
 FOR EACH ROW EXECUTE FUNCTION public.analysis_context_immutable();
CREATE FUNCTION public.analysis_result_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 RAISE EXCEPTION 'analysis result is immutable' USING ERRCODE='22023';
END $$;
CREATE TRIGGER analysis_result_immutable BEFORE UPDATE OR DELETE ON analysis_results
 FOR EACH ROW EXECUTE FUNCTION public.analysis_result_immutable();

CREATE FUNCTION access.analysis_admit(p_query jsonb,p_key text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE shop uuid; platform text; tenant bigint:=public.current_tenant_id();
 actor uuid:=public.current_actor_id(); member uuid; old public.analysis_tasks;
 identifier uuid:=gen_random_uuid(); event uuid:=gen_random_uuid(); canonical_query jsonb; digest text;
 start_date date; end_date date; scopes uuid[];
BEGIN
 IF (jsonb_typeof(p_query)='object' AND octet_length(p_query::text)<=4096
  AND p_query ?& ARRAY['shopId','platformId','startDate','endDate','dimensions','timezone']
  AND p_query-ARRAY['shopId','platformId','startDate','endDate','dimensions','timezone']='{}'::jsonb
  AND p_query->>'timezone'='Asia/Shanghai' AND jsonb_typeof(p_query->'dimensions')='array'
  AND p_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$') IS NOT TRUE THEN
  RAISE EXCEPTION 'analysis query invalid' USING ERRCODE='22023'; END IF;
 shop:=(p_query->>'shopId')::uuid; platform:=p_query->>'platformId';
 start_date:=(p_query->>'startDate')::date; end_date:=(p_query->>'endDate')::date;
 IF (end_date-start_date BETWEEN 0 AND 365
  AND p_query->>'startDate'=to_char(start_date,'YYYY-MM-DD')
  AND p_query->>'endDate'=to_char(end_date,'YYYY-MM-DD')
  AND jsonb_array_length(p_query->'dimensions') BETWEEN 1 AND 4
  AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_query->'dimensions') v
   WHERE jsonb_typeof(v)<>'string' OR v#>>'{}' NOT IN ('shop','live','ads','video'))
  AND (SELECT count(DISTINCT v) FROM jsonb_array_elements(p_query->'dimensions') v)
   =jsonb_array_length(p_query->'dimensions')) IS NOT TRUE THEN
  RAISE EXCEPTION 'analysis range invalid' USING ERRCODE='22023'; END IF;
 IF NOT access.analysis_context(shop,platform) OR NOT access.analysis_shop(shop,'task.create') THEN
  RAISE EXCEPTION 'analysis scope denied' USING ERRCODE='42501'; END IF;
 SELECT id INTO member FROM public.memberships WHERE tenant_id=tenant AND user_id=actor AND status='ACTIVE';
 canonical_query:=jsonb_set(p_query,'{dimensions}',
  (SELECT jsonb_agg(v ORDER BY v) FROM jsonb_array_elements(p_query->'dimensions') v));
 digest:=encode(sha256(convert_to(canonical_query::text,'UTF8')),'hex');
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text||'|'||actor::text||'|'||p_key,760401));
 SELECT * INTO old FROM public.analysis_tasks WHERE tenant_id=tenant AND user_id=actor AND idempotency_key=p_key;
 IF FOUND THEN
  IF old.query_digest<>digest THEN RAISE EXCEPTION 'analysis idempotency mismatch' USING ERRCODE='P0409'; END IF;
  IF NOT access.analysis_visible(old.tenant_id,old.user_id,old.shop_id,old.platform_id,old.account_scope)
   THEN RAISE EXCEPTION 'analysis original scope denied' USING ERRCODE='42501'; END IF;
  RETURN old.id;
 END IF;
 scopes:=access.analysis_accounts(shop,platform);
 INSERT INTO public.outbox_events(event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
 VALUES(event,tenant,'analysis',identifier::text,'yuliang.system.analysis_accept',jsonb_build_object(
  'event_id',event,'tenant_id',tenant,'job_id',identifier,'membership_id',member,'user_id',actor));
 INSERT INTO public.analysis_tasks(id,tenant_id,user_id,membership_id,shop_id,platform_id,account_scope,
  query_json,query_digest,idempotency_key,transport_event_id)
 VALUES(identifier,tenant,actor,member,shop,platform,scopes,p_query,digest,p_key,event);
 RETURN identifier;
END $$;
CREATE FUNCTION access.analysis_receive(p_event uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE task public.analysis_tasks; notice public.outbox_events;
BEGIN
 SELECT * INTO task FROM public.analysis_tasks WHERE transport_event_id=p_event
  AND tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id();
 IF NOT FOUND THEN RAISE EXCEPTION 'analysis event unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO notice FROM public.outbox_events WHERE event_id=p_event AND tenant_id=task.tenant_id
  AND aggregate_type='analysis' AND aggregate_id=task.id::text AND event_type='yuliang.system.analysis_accept';
 IF NOT FOUND OR notice.payload<>jsonb_build_object('event_id',p_event,'tenant_id',task.tenant_id,
  'job_id',task.id,'membership_id',task.membership_id,'user_id',task.user_id) THEN
  RAISE EXCEPTION 'analysis event mismatch' USING ERRCODE='42501'; END IF;
 INSERT INTO public.analysis_deliveries(tenant_id,task_id,event_id) VALUES(task.tenant_id,task.id,p_event)
 ON CONFLICT(tenant_id,task_id) DO NOTHING;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION access.analysis_shop(uuid,text),access.analysis_accounts(uuid,text),
 access.analysis_context(uuid,text),access.analysis_visible(bigint,uuid,uuid,text,uuid[]),
 access.analysis_admit(jsonb,text),access.analysis_receive(uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.analysis_shop(uuid,text),access.analysis_accounts(uuid,text),
 access.analysis_context(uuid,text),access.analysis_visible(bigint,uuid,uuid,text,uuid[]),
 access.analysis_admit(jsonb,text),access.analysis_receive(uuid) TO app_login;
