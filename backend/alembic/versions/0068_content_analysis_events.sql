ALTER TABLE workflow_runs ADD CONSTRAINT workflow_run_creator_key UNIQUE(tenant_id,created_by,id);
ALTER TABLE event_source_outbox ADD COLUMN analysis_task_id uuid,ADD COLUMN workflow_run_id uuid;
ALTER TABLE event_projections ADD COLUMN analysis_task_id uuid,ADD COLUMN workflow_run_id uuid;
ALTER TABLE event_source_outbox ADD CONSTRAINT source_analysis_fk
 FOREIGN KEY(tenant_id,scope_user_id,analysis_task_id) REFERENCES analysis_tasks(tenant_id,user_id,id);
ALTER TABLE event_source_outbox ADD CONSTRAINT source_workflow_fk
 FOREIGN KEY(tenant_id,scope_user_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,created_by,id);
ALTER TABLE event_projections ADD CONSTRAINT projection_analysis_fk
 FOREIGN KEY(tenant_id,analysis_task_id) REFERENCES analysis_tasks(tenant_id,id);
ALTER TABLE event_projections ADD CONSTRAINT projection_workflow_fk
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id);
ALTER TABLE event_source_outbox DROP CONSTRAINT ck_stream_entity_topic;
ALTER TABLE event_source_outbox ADD CONSTRAINT ck_stream_entity_topic CHECK(
 (entity_type,event_type) IN (('command_job','job.changed'),('approval','approval.changed'),
 ('execution','execution.unknown'),('capability_binding','capability.changed'),('sync_source','sync.completed'),
 ('analysis_task','analysis.changed'),('workflow_run','workflow.changed')));
ALTER TABLE event_source_outbox DROP CONSTRAINT ck_stream_source_scope;
ALTER TABLE event_source_outbox ADD CONSTRAINT ck_stream_source_scope CHECK((
 (entity_type IN ('command_job','approval','execution') AND job_id IS NOT NULL AND scope_user_id IS NOT NULL
  AND analysis_task_id IS NULL AND workflow_run_id IS NULL)
 OR (entity_type IN ('capability_binding','sync_source') AND job_id IS NULL AND scope_user_id IS NULL
  AND account_id IS NOT NULL AND permission_code IS NOT NULL AND analysis_task_id IS NULL AND workflow_run_id IS NULL)
 OR (entity_type='analysis_task' AND job_id IS NULL AND scope_user_id IS NOT NULL
  AND analysis_task_id IS NOT NULL AND entity_id=analysis_task_id AND workflow_run_id IS NULL
  AND account_id IS NULL AND permission_code IS NULL)
 OR (entity_type='workflow_run' AND job_id IS NULL AND scope_user_id IS NOT NULL
  AND workflow_run_id IS NOT NULL AND entity_id=workflow_run_id AND analysis_task_id IS NULL
  AND account_id IS NULL AND permission_code IS NULL)) IS TRUE);
ALTER TABLE event_projections ADD CONSTRAINT ck_projection_real_resource CHECK((
 (entity_type='analysis_task' AND event_type='analysis.changed' AND analysis_task_id IS NOT NULL
  AND entity_id=analysis_task_id AND workflow_run_id IS NULL AND job_id IS NULL
  AND account_id IS NULL AND permission_code IS NULL)
 OR (entity_type='workflow_run' AND event_type='workflow.changed' AND workflow_run_id IS NOT NULL
  AND entity_id=workflow_run_id AND analysis_task_id IS NULL AND job_id IS NULL
  AND account_id IS NULL AND permission_code IS NULL)
 OR (entity_type IN ('command_job','approval','execution','capability_binding','sync_source')
  AND analysis_task_id IS NULL AND workflow_run_id IS NULL)) IS TRUE);
ALTER TABLE event_resource_heads DROP CONSTRAINT event_resource_heads_entity_type_check;
ALTER TABLE event_resource_heads ADD CONSTRAINT event_resource_heads_entity_type_check
 CHECK(entity_type IN ('capability_binding','sync_source','analysis_task'));

CREATE FUNCTION public.capture_analysis_workflow_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE kind text; topic text; number bigint; actor uuid; state text;
BEGIN
 IF TG_TABLE_NAME='analysis_tasks' THEN
  IF TG_OP='UPDATE' AND (NEW.status,NEW.progress,NEW.result_id,NEW.error)
    IS NOT DISTINCT FROM (OLD.status,OLD.progress,OLD.result_id,OLD.error) THEN RETURN NEW; END IF;
  kind:='analysis_task';topic:='analysis.changed';actor:=NEW.user_id;state:=upper(NEW.status);
  INSERT INTO public.event_resource_heads AS head(tenant_id,entity_type,entity_id,version)
   VALUES(NEW.tenant_id,kind,NEW.id,1) ON CONFLICT(tenant_id,entity_type,entity_id)
   DO UPDATE SET version=head.version+1 RETURNING version INTO number;
 ELSE
  IF TG_OP='UPDATE' AND (NEW.revision,NEW.status) IS NOT DISTINCT FROM (OLD.revision,OLD.status)
   THEN RETURN NEW; END IF;
  IF TG_OP='UPDATE' AND NEW.revision<=OLD.revision THEN
   RAISE EXCEPTION 'workflow notice revision must increase' USING ERRCODE='40001'; END IF;
  kind:='workflow_run';topic:='workflow.changed';actor:=NEW.created_by;state:=NEW.status;number:=NEW.revision;
 END IF;
 INSERT INTO public.event_source_outbox(tenant_id,scope_user_id,entity_type,entity_id,version,event_type,
  changes,occurred_at,analysis_task_id,workflow_run_id)
 VALUES(NEW.tenant_id,actor,kind,NEW.id,number,topic,jsonb_build_object('status',state),clock_timestamp(),
  CASE WHEN kind='analysis_task' THEN NEW.id END,CASE WHEN kind='workflow_run' THEN NEW.id END);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.capture_analysis_workflow_event() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER stream_analysis_task AFTER INSERT OR UPDATE ON analysis_tasks
 FOR EACH ROW EXECUTE FUNCTION public.capture_analysis_workflow_event();
CREATE TRIGGER stream_workflow_run AFTER INSERT OR UPDATE ON workflow_runs
 FOR EACH ROW EXECUTE FUNCTION public.capture_analysis_workflow_event();

CREATE FUNCTION access.workflow_stream_visible(p_run uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.workflow_runs r
 JOIN public.commerce_products product ON (product.tenant_id,product.id,product.shop_id)=(r.tenant_id,r.product_id,r.shop_id)
 JOIN public.entity_links link ON (link.tenant_id,link.shop_id,link.account_id)=(product.tenant_id,product.shop_id,product.account_id)
 JOIN public.shops shop ON (shop.tenant_id,shop.id)=(link.tenant_id,link.shop_id)
 JOIN public.ad_accounts account ON (account.tenant_id,account.id)=(link.tenant_id,link.account_id)
 JOIN public.memberships m ON m.tenant_id=r.tenant_id AND m.user_id=public.current_actor_id()
 JOIN public.users u ON u.id=m.user_id JOIN public.tenants tenant ON tenant.id=m.tenant_id
 WHERE r.tenant_id=public.current_tenant_id() AND r.id=p_run AND product.deleted_at IS NULL
 AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
 AND tenant.status='ACTIVE' AND tenant.deleted_at IS NULL AND shop.deleted_at IS NULL
 AND account.deleted_at IS NULL AND account.status IN ('BOUND','AUTHORIZED','DEGRADED')
 AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
  ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
  WHERE g.membership_id=m.id AND rp.permission_code='overview.read')
 AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.tenant_id=m.tenant_id AND ds.membership_id=m.id
  AND (ds.permissions_mask IS NULL OR 'overview.read'=ANY(ds.permissions_mask))
  AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=shop.id OR ds.entity_id=shop.entity_id))
 AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.tenant_id=m.tenant_id AND ds.membership_id=m.id
  AND (ds.permissions_mask IS NULL OR 'overview.read'=ANY(ds.permissions_mask))
  AND (ds.scope_type='ALL_TENANT' OR ds.account_id=account.id OR ds.entity_id=account.entity_id)))
$$;
REVOKE ALL ON FUNCTION access.workflow_stream_visible(uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.workflow_stream_visible(uuid) TO app_login;

CREATE FUNCTION access.stream_resource_visible(p_job uuid,p_topic text,p_account uuid,p_permission text,
 p_analysis uuid,p_workflow uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT CASE WHEN p_topic='analysis.changed' AND p_analysis IS NOT NULL AND p_workflow IS NULL AND p_job IS NULL
 THEN EXISTS(SELECT 1 FROM public.analysis_tasks task WHERE task.id=p_analysis
  AND access.analysis_visible(task.tenant_id,task.user_id,task.shop_id,task.platform_id,task.account_scope))
 WHEN p_topic='workflow.changed' AND p_workflow IS NOT NULL AND p_analysis IS NULL AND p_job IS NULL
 THEN access.workflow_stream_visible(p_workflow)
 WHEN p_analysis IS NULL AND p_workflow IS NULL
 THEN access.stream_event_visible(p_job,p_topic,p_account,p_permission) ELSE false END
$$;
REVOKE ALL ON FUNCTION access.stream_resource_visible(uuid,text,uuid,text,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.stream_resource_visible(uuid,text,uuid,text,uuid,uuid) TO app_login;
DROP POLICY current_scope ON event_projections;
CREATE POLICY current_scope ON event_projections FOR SELECT TO app_login USING(
 tenant_id=public.current_tenant_id() AND access.stream_resource_visible(
 job_id,event_type,account_id,permission_code,analysis_task_id,workflow_run_id));

CREATE OR REPLACE FUNCTION access.project_core_events(p_limit integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); head public.event_stream_heads;
 source public.event_source_outbox; total integer:=0;
BEGIN
 IF tenant IS NULL OR p_limit IS NULL OR p_limit<1 OR p_limit>500 THEN
  RAISE EXCEPTION 'invalid projector batch' USING ERRCODE='22023'; END IF;
 INSERT INTO public.event_stream_heads(tenant_id) VALUES(tenant) ON CONFLICT DO NOTHING;
 SELECT * INTO STRICT head FROM public.event_stream_heads WHERE tenant_id=tenant FOR UPDATE;
 FOR source IN SELECT * FROM public.event_source_outbox WHERE tenant_id=tenant AND projected_seq IS NULL
   ORDER BY id LIMIT p_limit FOR UPDATE LOOP
  head.head_seq:=head.head_seq+1;
  INSERT INTO public.event_projections(tenant_id,tenant_seq,source_event_id,job_id,scope_key,
   entity_type,entity_id,version,event_type,payload,account_id,permission_code,analysis_task_id,workflow_run_id)
  VALUES(tenant,head.head_seq,source.event_id,source.job_id,
   CASE WHEN source.scope_user_id IS NOT NULL THEN 'user:'||source.scope_user_id::text
    ELSE 'account:'||source.account_id::text END,
   source.entity_type,source.entity_id,source.version,source.event_type,
   jsonb_build_object('tenantId',tenant::text,'entityType',source.entity_type,'entityId',source.entity_id,
    'version',source.version,'changes',source.changes,'at',source.occurred_at),
   source.account_id,source.permission_code,source.analysis_task_id,source.workflow_run_id);
  UPDATE public.event_source_outbox SET projected_seq=head.head_seq WHERE id=source.id;
  total:=total+1;
 END LOOP;
 UPDATE public.event_stream_heads SET head_seq=head.head_seq WHERE tenant_id=tenant;
 RETURN total;
END $$;
