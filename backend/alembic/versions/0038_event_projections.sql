-- 业务通知独立于既有工作队列Outbox；由实际业务表触发器原子生产，不能由HTTP伪造。
CREATE TABLE event_source_outbox (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,event_id uuid NOT NULL DEFAULT gen_random_uuid(),
 tenant_id bigint NOT NULL REFERENCES tenants(id),job_id uuid NOT NULL,
 scope_user_id uuid NOT NULL,entity_type varchar(30) NOT NULL,entity_id uuid NOT NULL,
 version bigint NOT NULL CHECK(version>0 AND version<=9007199254740991),
 event_type varchar(60) NOT NULL,changes jsonb NOT NULL,occurred_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),projected_seq bigint CHECK(projected_seq>0),
 UNIQUE(tenant_id,event_id),UNIQUE(tenant_id,entity_type,entity_id,version,event_type),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id),
 FOREIGN KEY(tenant_id,scope_user_id) REFERENCES memberships(tenant_id,user_id),
 CHECK((entity_type,event_type) IN (('command_job','job.changed'),('approval','approval.changed'),
  ('execution','execution.unknown'))),
 CHECK(jsonb_typeof(changes)='object' AND changes ? 'status'
  AND (changes-'status')='{}'::jsonb AND jsonb_typeof(changes->'status')='string')
);
CREATE INDEX ix_event_source_pending ON event_source_outbox(tenant_id,id) WHERE projected_seq IS NULL;
CREATE TABLE event_stream_heads (
 tenant_id bigint PRIMARY KEY REFERENCES tenants(id),head_seq bigint NOT NULL DEFAULT 0 CHECK(head_seq>=0),
 retained_from_seq bigint NOT NULL DEFAULT 1 CHECK(retained_from_seq>=1 AND retained_from_seq<=head_seq+1)
);
CREATE TABLE event_projections (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,tenant_id bigint NOT NULL REFERENCES tenants(id),
 tenant_seq bigint NOT NULL CHECK(tenant_seq>0),source_event_id uuid NOT NULL,job_id uuid NOT NULL,
 scope_key varchar(120) NOT NULL,entity_type varchar(30) NOT NULL,entity_id uuid NOT NULL,
 version bigint NOT NULL,event_type varchar(60) NOT NULL,payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,tenant_seq),UNIQUE(tenant_id,source_event_id),
 FOREIGN KEY(tenant_id,source_event_id) REFERENCES event_source_outbox(tenant_id,event_id),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id)
);
CREATE INDEX ix_event_projection_scope ON event_projections(tenant_id,scope_key,tenant_seq);

CREATE FUNCTION public.capture_core_stream_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE job public.command_jobs; kind text; topic text; state text; number bigint; stamp timestamptz;
BEGIN
 IF TG_TABLE_NAME='command_jobs' THEN
  IF NEW.document IS NULL THEN RETURN NEW; END IF;
  IF TG_OP='UPDATE' AND NEW.version=OLD.version AND NEW.status=OLD.status THEN RETURN NEW; END IF;
  job:=NEW;kind:='command_job';topic:='job.changed';state:=NEW.status;number:=NEW.version;stamp:=NEW.updated_at;
 ELSIF TG_TABLE_NAME='approvals' THEN
  IF NEW.command_job_id IS NULL THEN RETURN NEW; END IF;
  IF TG_OP='UPDATE' AND NEW.version=OLD.version AND NEW.state=OLD.state THEN RETURN NEW; END IF;
  SELECT * INTO STRICT job FROM public.command_jobs WHERE tenant_id=NEW.tenant_id AND id=NEW.command_job_id;
  kind:='approval';topic:='approval.changed';state:=NEW.state;number:=NEW.version;stamp:=NEW.updated_at;
 ELSE
  IF NEW.command_job_id IS NULL OR NEW.status<>'UNKNOWN' THEN RETURN NEW; END IF;
  IF TG_OP='UPDATE' AND NEW.version=OLD.version AND NEW.status=OLD.status THEN RETURN NEW; END IF;
  SELECT * INTO STRICT job FROM public.command_jobs WHERE tenant_id=NEW.tenant_id AND id=NEW.command_job_id;
  kind:='execution';topic:='execution.unknown';state:=NEW.status;number:=NEW.version;stamp:=NEW.updated_at;
 END IF;
 INSERT INTO public.event_source_outbox(tenant_id,job_id,scope_user_id,entity_type,entity_id,
  version,event_type,changes,occurred_at)
 VALUES(job.tenant_id,job.id,job.created_by,kind,NEW.id,number,topic,jsonb_build_object('status',state),stamp)
 ON CONFLICT(tenant_id,entity_type,entity_id,version,event_type) DO NOTHING;
 IF NOT FOUND AND EXISTS(SELECT 1 FROM public.event_source_outbox e WHERE e.tenant_id=job.tenant_id
   AND e.entity_type=kind AND e.entity_id=NEW.id AND e.version=number AND e.event_type=topic
   AND e.changes IS DISTINCT FROM jsonb_build_object('status',state)) THEN
  RAISE EXCEPTION 'event version content changed' USING ERRCODE='40001'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.capture_core_stream_event() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER stream_job AFTER INSERT OR UPDATE ON command_jobs FOR EACH ROW
 EXECUTE FUNCTION public.capture_core_stream_event();
CREATE TRIGGER stream_approval AFTER INSERT OR UPDATE ON approvals FOR EACH ROW
 EXECUTE FUNCTION public.capture_core_stream_event();
CREATE TRIGGER stream_execution AFTER INSERT OR UPDATE ON execution_orders FOR EACH ROW
 EXECUTE FUNCTION public.capture_core_stream_event();

-- 返回布尔，撤销后的事件过滤不抛出一串历史资源信息；仍逐次查当前资格和双范围。
CREATE FUNCTION access.stream_event_visible(p_job uuid,p_topic text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.command_jobs j JOIN public.memberships m
  ON m.tenant_id=j.tenant_id AND m.user_id=public.current_actor_id()
  JOIN public.users u ON u.id=m.user_id JOIN public.tenants t ON t.id=m.tenant_id
  CROSS JOIN LATERAL(SELECT CASE WHEN j.created_by=m.user_id THEN 'task.create'
    WHEN p_topic='approval.changed' THEN 'task.approve' ELSE NULL END AS permission) gate
  WHERE j.tenant_id=public.current_tenant_id() AND j.id=p_job AND j.document IS NOT NULL
   AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND t.status='ACTIVE' AND t.deleted_at IS NULL AND gate.permission IS NOT NULL
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
    WHERE g.tenant_id=j.tenant_id AND g.membership_id=m.id AND rp.permission_code=gate.permission)
   AND EXISTS(SELECT 1 FROM public.shops s JOIN public.data_scopes ds ON ds.tenant_id=s.tenant_id
    WHERE s.tenant_id=j.tenant_id AND s.id=j.shop_id AND s.deleted_at IS NULL AND ds.membership_id=m.id
     AND (ds.permissions_mask IS NULL OR gate.permission=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id))
   AND (j.account_id IS NULL OR EXISTS(SELECT 1 FROM public.ad_accounts a JOIN public.data_scopes ds
    ON ds.tenant_id=a.tenant_id WHERE a.tenant_id=j.tenant_id AND a.id=j.account_id AND a.deleted_at IS NULL
     AND ds.membership_id=m.id AND (ds.permissions_mask IS NULL OR gate.permission=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.account_id=a.id OR ds.entity_id=a.entity_id))))
$$;

DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['event_source_outbox','event_stream_heads','event_projections'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
 END LOOP;
END $$;
CREATE POLICY tenant_head ON event_stream_heads FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id());
CREATE POLICY current_scope ON event_projections FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND access.stream_event_visible(job_id,event_type));
GRANT SELECT ON event_stream_heads,event_projections TO app_login;

CREATE FUNCTION access.project_core_events(p_limit integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); head public.event_stream_heads;
 source public.event_source_outbox; total integer:=0;
BEGIN
 IF tenant IS NULL OR p_limit IS NULL OR p_limit<1 OR p_limit>500 THEN
  RAISE EXCEPTION 'invalid projector batch' USING ERRCODE='22023'; END IF;
 INSERT INTO public.event_stream_heads(tenant_id) VALUES(tenant) ON CONFLICT DO NOTHING;
 SELECT * INTO STRICT head FROM public.event_stream_heads WHERE tenant_id=tenant FOR UPDATE;
 -- 只扫未投影的已提交源；id仅用于公平排序，绝不保存全局identity游标。
 FOR source IN SELECT * FROM public.event_source_outbox WHERE tenant_id=tenant AND projected_seq IS NULL
   ORDER BY id LIMIT p_limit FOR UPDATE LOOP
  head.head_seq:=head.head_seq+1;
  INSERT INTO public.event_projections(tenant_id,tenant_seq,source_event_id,job_id,scope_key,
   entity_type,entity_id,version,event_type,payload)
  VALUES(tenant,head.head_seq,source.event_id,source.job_id,'user:'||source.scope_user_id::text,
   source.entity_type,source.entity_id,source.version,source.event_type,
   jsonb_build_object('tenantId',tenant::text,'entityType',source.entity_type,'entityId',source.entity_id,
    'version',source.version,'changes',source.changes,'at',source.occurred_at));
  UPDATE public.event_source_outbox SET projected_seq=head.head_seq WHERE id=source.id;
  total:=total+1;
 END LOOP;
 UPDATE public.event_stream_heads SET head_seq=head.head_seq WHERE tenant_id=tenant;
 RETURN total;
END $$;

CREATE FUNCTION access.prune_core_events() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); head public.event_stream_heads;
 floor bigint; total integer;
BEGIN
 SELECT * INTO head FROM public.event_stream_heads WHERE tenant_id=tenant FOR UPDATE;
 IF NOT FOUND THEN RETURN 0; END IF;
 SELECT coalesce(min(tenant_seq),head.head_seq+1) INTO floor FROM public.event_projections
  WHERE tenant_id=tenant AND created_at>=clock_timestamp()-interval '7 days';
 -- 仅删过期前缀；发生时钟调整也不删除较新的投影。
 DELETE FROM public.event_projections WHERE tenant_id=tenant AND tenant_seq<floor;
 GET DIAGNOSTICS total=ROW_COUNT;
 UPDATE public.event_stream_heads SET retained_from_seq=greatest(retained_from_seq,floor) WHERE tenant_id=tenant;
 -- 源Outbox与已投影序号继续保留，防止清理后旧消息复活。
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION access.stream_event_visible(uuid,text),access.project_core_events(integer),
 access.prune_core_events() FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.stream_event_visible(uuid,text),access.project_core_events(integer),
 access.prune_core_events() TO app_login;
