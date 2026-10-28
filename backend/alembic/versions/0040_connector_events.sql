ALTER TABLE event_source_outbox ALTER COLUMN job_id DROP NOT NULL;
ALTER TABLE event_source_outbox ALTER COLUMN scope_user_id DROP NOT NULL;
ALTER TABLE event_source_outbox ADD COLUMN account_id uuid,ADD COLUMN permission_code varchar(80),
 ADD FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 ADD FOREIGN KEY(permission_code) REFERENCES permissions(code);
DO $$ DECLARE item record; BEGIN
 FOR item IN SELECT conname FROM pg_constraint WHERE conrelid='public.event_source_outbox'::regclass
  AND contype='c' AND pg_get_constraintdef(oid) LIKE '%entity_type%'
  AND pg_get_constraintdef(oid) LIKE '%event_type%' LOOP
  EXECUTE format('ALTER TABLE public.event_source_outbox DROP CONSTRAINT %I',item.conname);
 END LOOP;
END $$;
ALTER TABLE event_source_outbox ADD CONSTRAINT ck_stream_entity_topic CHECK(
 (entity_type,event_type) IN (('command_job','job.changed'),('approval','approval.changed'),
 ('execution','execution.unknown'),('capability_binding','capability.changed'),('sync_source','sync.completed')));
ALTER TABLE event_source_outbox ADD CONSTRAINT ck_stream_source_scope CHECK(
 (entity_type IN ('command_job','approval','execution') AND job_id IS NOT NULL AND scope_user_id IS NOT NULL)
 OR (entity_type IN ('capability_binding','sync_source') AND job_id IS NULL AND scope_user_id IS NULL
   AND account_id IS NOT NULL AND permission_code IS NOT NULL));
ALTER TABLE event_projections ALTER COLUMN job_id DROP NOT NULL;
ALTER TABLE event_projections ADD COLUMN account_id uuid,ADD COLUMN permission_code varchar(80),
 ADD FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 ADD FOREIGN KEY(permission_code) REFERENCES permissions(code);
CREATE INDEX ix_event_projection_retention ON event_projections(tenant_id,created_at);

-- 通知修订独立于不可变的绑定/请求合同revision，也独立于同步分页游标version。
CREATE TABLE event_resource_heads (
 tenant_id bigint NOT NULL REFERENCES tenants(id),entity_type varchar(30) NOT NULL,
 entity_id uuid NOT NULL,version bigint NOT NULL CHECK(version>0 AND version<=9007199254740991),
 PRIMARY KEY(tenant_id,entity_type,entity_id),CHECK(entity_type IN ('capability_binding','sync_source'))
);
CREATE TABLE capability_event_deadlines (
 assertion_id uuid PRIMARY KEY REFERENCES capability_assertions(id),tenant_id bigint NOT NULL,
 binding_id uuid NOT NULL,due_at timestamptz NOT NULL,emitted_at timestamptz,
 FOREIGN KEY(tenant_id,binding_id) REFERENCES capability_bindings(tenant_id,id)
);
CREATE INDEX ix_capability_event_due ON capability_event_deadlines(tenant_id,due_at) WHERE emitted_at IS NULL;
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['event_resource_heads','capability_event_deadlines'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
 END LOOP;
END $$;

CREATE FUNCTION access.emit_connector_notice(p_tenant bigint,p_type text,p_id uuid,p_account uuid,
 p_permission text,p_topic text,p_status text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE number bigint;
BEGIN
 INSERT INTO public.event_resource_heads AS head(tenant_id,entity_type,entity_id,version)
 VALUES(p_tenant,p_type,p_id,1) ON CONFLICT(tenant_id,entity_type,entity_id)
 DO UPDATE SET version=head.version+1 RETURNING version INTO number;
 INSERT INTO public.event_source_outbox(tenant_id,entity_type,entity_id,version,event_type,
  account_id,permission_code,changes,occurred_at)
 VALUES(p_tenant,p_type,p_id,number,p_topic,p_account,p_permission,
  jsonb_build_object('status',p_status),clock_timestamp());
END $$;
REVOKE ALL ON FUNCTION access.emit_connector_notice(bigint,text,uuid,uuid,text,text,text)
 FROM PUBLIC,app_login,app_auth;

CREATE FUNCTION public.capture_connector_stream_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE binding public.capability_bindings; source public.sync_sources;
BEGIN
 IF TG_TABLE_NAME='capability_assertions' THEN
  SELECT * INTO STRICT binding FROM public.capability_bindings WHERE tenant_id=NEW.tenant_id AND id=NEW.binding_id;
  PERFORM access.emit_connector_notice(binding.tenant_id,'capability_binding',binding.id,binding.account_id,
   binding.permission_code,'capability.changed',CASE WHEN binding.active THEN 'CHANGED' ELSE 'INACTIVE' END);
  INSERT INTO public.capability_event_deadlines(assertion_id,tenant_id,binding_id,due_at)
  VALUES(NEW.id,NEW.tenant_id,NEW.binding_id,NEW.expires_at);
 ELSIF TG_TABLE_NAME='capability_bindings' THEN
  IF TG_OP='UPDATE' AND NEW.active=OLD.active THEN RETURN NEW; END IF;
  PERFORM access.emit_connector_notice(NEW.tenant_id,'capability_binding',NEW.id,NEW.account_id,
   NEW.permission_code,'capability.changed',CASE WHEN NEW.active THEN 'CHANGED' ELSE 'INACTIVE' END);
 ELSE
  IF NEW.state='RUNNING' OR (TG_OP='UPDATE' AND NEW.state=OLD.state) THEN RETURN NEW; END IF;
  SELECT * INTO STRICT source FROM public.sync_sources WHERE tenant_id=NEW.tenant_id AND id=NEW.source_id;
  SELECT * INTO STRICT binding FROM public.capability_bindings WHERE tenant_id=source.tenant_id AND id=source.binding_id;
  PERFORM access.emit_connector_notice(source.tenant_id,'sync_source',source.id,source.account_id,
   binding.permission_code,'sync.completed',NEW.state);
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.capture_connector_stream_event() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER stream_binding AFTER INSERT OR UPDATE OF active ON capability_bindings
 FOR EACH ROW EXECUTE FUNCTION public.capture_connector_stream_event();
CREATE TRIGGER stream_assertion AFTER INSERT ON capability_assertions
 FOR EACH ROW EXECUTE FUNCTION public.capture_connector_stream_event();
CREATE TRIGGER stream_sync_run AFTER INSERT OR UPDATE OF state ON sync_runs
 FOR EACH ROW EXECUTE FUNCTION public.capture_connector_stream_event();

-- 升级前仍有效或已过期的证据均登记截止；按实际到期捕获一次，不预分配未来通知版本。
INSERT INTO capability_event_deadlines(assertion_id,tenant_id,binding_id,due_at)
 SELECT id,tenant_id,binding_id,expires_at FROM capability_assertions;
CREATE FUNCTION access.capture_due_capabilities(p_limit integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE deadline public.capability_event_deadlines; binding public.capability_bindings; total integer:=0;
BEGIN
 IF public.current_tenant_id() IS NULL OR p_limit IS NULL OR p_limit<1 OR p_limit>500 THEN
  RAISE EXCEPTION 'invalid deadline batch' USING ERRCODE='22023'; END IF;
 FOR deadline IN SELECT * FROM public.capability_event_deadlines
  WHERE tenant_id=public.current_tenant_id() AND emitted_at IS NULL AND due_at<=clock_timestamp()
  ORDER BY due_at,assertion_id LIMIT p_limit FOR UPDATE SKIP LOCKED LOOP
  SELECT * INTO STRICT binding FROM public.capability_bindings
   WHERE tenant_id=deadline.tenant_id AND id=deadline.binding_id;
  PERFORM access.emit_connector_notice(binding.tenant_id,'capability_binding',binding.id,binding.account_id,
   binding.permission_code,'capability.changed',CASE WHEN binding.active THEN 'CHANGED' ELSE 'INACTIVE' END);
  UPDATE public.capability_event_deadlines SET emitted_at=clock_timestamp() WHERE assertion_id=deadline.assertion_id;
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION access.capture_due_capabilities(integer) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.capture_due_capabilities(integer) TO app_login;

CREATE FUNCTION access.stream_event_visible(p_job uuid,p_topic text,p_account uuid,p_permission text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT CASE WHEN p_topic IN ('job.changed','approval.changed','execution.unknown')
  THEN access.stream_event_visible(p_job,p_topic)
  WHEN p_topic IN ('capability.changed','sync.completed') AND p_job IS NULL THEN EXISTS(
   SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
   JOIN public.tenants t ON t.id=m.tenant_id JOIN public.ad_accounts a ON a.tenant_id=m.tenant_id
   WHERE m.tenant_id=public.current_tenant_id() AND m.user_id=public.current_actor_id()
    AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
    AND t.status='ACTIVE' AND t.deleted_at IS NULL AND a.id=p_account AND a.deleted_at IS NULL
    AND a.status IN ('BOUND','AUTHORIZED','DEGRADED')
    AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
     ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
     WHERE g.membership_id=m.id AND rp.permission_code=p_permission)
    AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.membership_id=m.id
     AND (ds.permissions_mask IS NULL OR p_permission=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.account_id=a.id OR ds.entity_id=a.entity_id))
    AND EXISTS(SELECT 1 FROM public.entity_links link JOIN public.shops s
     ON (s.tenant_id,s.id)=(link.tenant_id,link.shop_id) JOIN public.data_scopes ds ON ds.tenant_id=s.tenant_id
     WHERE link.tenant_id=m.tenant_id AND link.account_id=a.id AND s.deleted_at IS NULL
      AND ds.membership_id=m.id AND (ds.permissions_mask IS NULL OR p_permission=ANY(ds.permissions_mask))
      AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id)))
  ELSE false END
$$;
REVOKE ALL ON FUNCTION access.stream_event_visible(uuid,text,uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.stream_event_visible(uuid,text,uuid,text) TO app_login;
DROP POLICY current_scope ON event_projections;
CREATE POLICY current_scope ON event_projections FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id()
  AND access.stream_event_visible(job_id,event_type,account_id,permission_code));

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
   entity_type,entity_id,version,event_type,payload,account_id,permission_code)
  VALUES(tenant,head.head_seq,source.event_id,source.job_id,
   CASE WHEN source.scope_user_id IS NOT NULL THEN 'user:'||source.scope_user_id::text
    ELSE 'account:'||source.account_id::text END,
   source.entity_type,source.entity_id,source.version,source.event_type,
   jsonb_build_object('tenantId',tenant::text,'entityType',source.entity_type,'entityId',source.entity_id,
    'version',source.version,'changes',source.changes,'at',source.occurred_at),
   source.account_id,source.permission_code);
  UPDATE public.event_source_outbox SET projected_seq=head.head_seq WHERE id=source.id;
  total:=total+1;
 END LOOP;
 UPDATE public.event_stream_heads SET head_seq=head.head_seq WHERE tenant_id=tenant;
 RETURN total;
END $$;
