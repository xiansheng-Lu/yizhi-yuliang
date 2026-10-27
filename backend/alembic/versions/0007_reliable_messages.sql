CREATE TABLE outbox_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 event_id uuid NOT NULL UNIQUE,
 tenant_id bigint NOT NULL REFERENCES tenants(id),
 aggregate_type varchar(30) NOT NULL,
 aggregate_id varchar(64) NOT NULL,
 event_type varchar(60) NOT NULL CHECK (event_type IN (
   'yuliang.system.identity_probe','yuliang.system.schedule_recovery',
   'yuliang.execute.guard','yuliang.platform.guard','yuliang.ai_media.guard')),
 payload jsonb NOT NULL,
 state varchar(10) NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','PUBLISHED')),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
 lease_owner uuid, lease_until timestamptz,
 last_error_code varchar(40),
 created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
 UNIQUE(tenant_id,event_id),
 CHECK (jsonb_typeof(payload)='object'
   AND payload ?& ARRAY['event_id','tenant_id','job_id','membership_id','user_id']
   AND (payload - ARRAY['event_id','tenant_id','job_id','membership_id','user_id'])='{}'::jsonb
   AND jsonb_typeof(payload->'event_id')='string' AND jsonb_typeof(payload->'job_id')='string'
   AND jsonb_typeof(payload->'membership_id')='string' AND jsonb_typeof(payload->'user_id')='string'
   AND (payload->>'membership_id')::uuid IS NOT NULL AND (payload->>'user_id')::uuid IS NOT NULL
   AND (payload->>'job_id')::uuid IS NOT NULL
   AND (payload->>'event_id')::uuid=event_id
   AND jsonb_typeof(payload->'tenant_id')='number'
   AND payload->>'tenant_id'=tenant_id::text AND payload->>'job_id'=aggregate_id)
);
CREATE INDEX ix_outbox_pending ON outbox_events(id) WHERE state='PENDING';
CREATE TABLE consumer_inbox (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 consumer varchar(60) NOT NULL CHECK (length(consumer)>0),
 event_id uuid NOT NULL REFERENCES outbox_events(event_id),
 received_at timestamptz NOT NULL DEFAULT now(), processed_at timestamptz,
 UNIQUE(consumer,event_id)
);
REVOKE ALL ON consumer_inbox FROM app_login,app_auth,PUBLIC;
CREATE TABLE scheduled_tasks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id bigint NOT NULL REFERENCES tenants(id),
 kind varchar(40) NOT NULL,
 business_key varchar(120) NOT NULL,
 state varchar(12) NOT NULL DEFAULT 'DUE'
   CHECK (state IN ('DUE','CLAIMED','DONE','FAILED','RESCHEDULED')),
 payload jsonb NOT NULL, due_at timestamptz NOT NULL,
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
 max_attempts integer NOT NULL DEFAULT 8 CHECK (max_attempts>0),
 lease_owner varchar(80), lease_until timestamptz, last_error varchar(300),
 source_event_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,kind,business_key),
 FOREIGN KEY(tenant_id,source_event_id) REFERENCES outbox_events(tenant_id,event_id)
);
CREATE INDEX ix_scheduled_due ON scheduled_tasks(state,due_at) WHERE state IN ('DUE','RESCHEDULED');
CREATE TRIGGER trg_scheduled_updated BEFORE UPDATE ON scheduled_tasks
 FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_owner ON outbox_events FOR ALL TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY outbox_select ON outbox_events FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
CREATE POLICY outbox_insert ON outbox_events FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
GRANT SELECT,INSERT ON outbox_events TO app_login;
GRANT USAGE ON SEQUENCE outbox_events_id_seq TO app_login;
ALTER TABLE scheduled_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduled_tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY scheduled_owner ON scheduled_tasks FOR ALL TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY scheduled_select ON scheduled_tasks FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id());
CREATE POLICY scheduled_insert ON scheduled_tasks FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id());
CREATE POLICY scheduled_update ON scheduled_tasks FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
GRANT SELECT,INSERT,UPDATE ON scheduled_tasks TO app_login;

CREATE FUNCTION access.outbox_claim(p_event uuid,p_owner uuid)
RETURNS TABLE(event_type varchar,payload jsonb)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 UPDATE public.outbox_events e SET lease_owner=p_owner,lease_until=statement_timestamp()+interval '30 seconds',
 attempts=attempts+1,last_error_code=NULL
 WHERE e.event_id=p_event AND e.tenant_id=public.current_tenant_id() AND e.state='PENDING'
   AND (e.lease_until IS NULL OR e.lease_until<=statement_timestamp())
 RETURNING e.event_type,e.payload;
$$;
CREATE FUNCTION access.outbox_finish(p_event uuid,p_owner uuid,p_success boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE changed uuid;
BEGIN
 UPDATE public.outbox_events SET state=CASE WHEN p_success THEN 'PUBLISHED' ELSE 'PENDING' END,
 published_at=CASE WHEN p_success THEN statement_timestamp() ELSE NULL END,
 lease_owner=NULL,lease_until=NULL,last_error_code=CASE WHEN p_success THEN NULL ELSE 'MQ_UNAVAILABLE' END
 WHERE event_id=p_event AND tenant_id=public.current_tenant_id() AND state='PENDING' AND lease_owner=p_owner
 RETURNING event_id INTO changed;
 RETURN changed IS NOT NULL;
END; $$;
CREATE FUNCTION access.inbox_receive(p_consumer text,p_event uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM public.outbox_events WHERE event_id=p_event
                AND tenant_id=public.current_tenant_id()) THEN
   RAISE EXCEPTION 'event scope denied' USING ERRCODE='42501'; END IF;
 INSERT INTO public.consumer_inbox(consumer,event_id) VALUES(p_consumer,p_event)
 ON CONFLICT(consumer,event_id) DO NOTHING;
 RETURN EXISTS (SELECT 1 FROM public.consumer_inbox WHERE consumer=p_consumer AND event_id=p_event
                AND processed_at IS NOT NULL);
END; $$;
CREATE FUNCTION access.inbox_lock(p_consumer text,p_event uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE stamp timestamptz;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM public.outbox_events WHERE event_id=p_event
                AND tenant_id=public.current_tenant_id()) THEN
   RAISE EXCEPTION 'event scope denied' USING ERRCODE='42501'; END IF;
 SELECT processed_at INTO stamp FROM public.consumer_inbox WHERE consumer=p_consumer AND event_id=p_event FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'inbox not received' USING ERRCODE='22023'; END IF;
 RETURN stamp IS NOT NULL;
END; $$;
CREATE FUNCTION access.inbox_processed(p_consumer text,p_event uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM public.outbox_events WHERE event_id=p_event
                AND tenant_id=public.current_tenant_id()) THEN
   RAISE EXCEPTION 'event scope denied' USING ERRCODE='42501'; END IF;
 UPDATE public.consumer_inbox SET processed_at=statement_timestamp()
 WHERE consumer=p_consumer AND event_id=p_event AND processed_at IS NULL;
END; $$;
CREATE FUNCTION access.outbox_reopen_recovery(p_event uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE changed uuid;
BEGIN
 -- 消费者已经持久完成比发布确认更强：修复确认后崩溃遗留的PENDING，不再次投递。
 UPDATE public.outbox_events e SET state='PUBLISHED',published_at=coalesce(e.published_at,statement_timestamp()),
   lease_owner=NULL,lease_until=NULL,last_error_code=NULL
 WHERE e.event_id=p_event AND e.tenant_id=public.current_tenant_id() AND e.state='PENDING'
   AND e.event_type='yuliang.system.schedule_recovery'
   AND EXISTS (SELECT 1 FROM public.consumer_inbox i WHERE i.event_id=e.event_id
               AND i.consumer='system.recovery' AND i.processed_at IS NOT NULL);
 IF FOUND THEN RETURN false; END IF;
 UPDATE public.outbox_events e SET state='PENDING',published_at=NULL,lease_owner=NULL,lease_until=NULL,
   last_error_code='RECOVERY_REQUIRED'
 WHERE e.event_id=p_event AND e.tenant_id=public.current_tenant_id()
   AND e.event_type='yuliang.system.schedule_recovery'
   AND coalesce(e.published_at,e.created_at)<statement_timestamp()-interval '30 seconds'
   AND (e.lease_until IS NULL OR e.lease_until<=statement_timestamp())
   AND NOT EXISTS (SELECT 1 FROM public.consumer_inbox i WHERE i.event_id=e.event_id
                   AND i.consumer='system.recovery' AND i.processed_at IS NOT NULL)
 RETURNING e.event_id INTO changed;
 RETURN changed IS NOT NULL;
END; $$;
CREATE FUNCTION access.outbox_candidates(p_limit integer)
RETURNS TABLE(event_id uuid,event_type varchar)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF p_limit NOT BETWEEN 1 AND 100 THEN
   RAISE EXCEPTION 'batch size invalid' USING ERRCODE='22023'; END IF;
 RETURN QUERY SELECT e.event_id,e.event_type FROM public.outbox_events e
 WHERE e.tenant_id=public.current_tenant_id() AND (
   e.state='PENDING' OR (e.event_type='yuliang.system.schedule_recovery'
    AND e.published_at<statement_timestamp()-interval '30 seconds'
    AND NOT EXISTS (SELECT 1 FROM public.consumer_inbox i WHERE i.event_id=e.event_id
      AND i.consumer='system.recovery' AND i.processed_at IS NOT NULL)))
 ORDER BY e.id LIMIT p_limit;
END; $$;
REVOKE ALL ON FUNCTION access.outbox_claim(uuid,uuid),access.outbox_finish(uuid,uuid,boolean),
 access.inbox_receive(text,uuid),access.inbox_lock(text,uuid),access.inbox_processed(text,uuid),
 access.outbox_reopen_recovery(uuid),access.outbox_candidates(integer) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.outbox_claim(uuid,uuid),access.outbox_finish(uuid,uuid,boolean),
 access.inbox_receive(text,uuid),access.inbox_lock(text,uuid),access.inbox_processed(text,uuid),
 access.outbox_reopen_recovery(uuid),access.outbox_candidates(integer) TO app_login;
