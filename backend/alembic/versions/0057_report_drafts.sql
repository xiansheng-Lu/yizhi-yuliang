CREATE TABLE report_drafts (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,user_id uuid NOT NULL,analysis_id uuid NOT NULL,
 version bigint NOT NULL CHECK(version>0),payload jsonb NOT NULL,
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,analysis_id) REFERENCES analysis_results(tenant_id,id)
);
CREATE TABLE report_draft_versions (
 tenant_id bigint NOT NULL,draft_id uuid NOT NULL,version bigint NOT NULL,payload jsonb NOT NULL,
 PRIMARY KEY(tenant_id,draft_id,version),
 FOREIGN KEY(tenant_id,draft_id) REFERENCES report_drafts(tenant_id,id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['report_drafts','report_draft_versions'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE POLICY own_draft ON report_drafts FOR SELECT TO app_login USING(
 user_id=public.current_actor_id() AND tenant_id=public.current_tenant_id()
 AND EXISTS(SELECT 1 FROM analysis_results r WHERE (r.tenant_id,r.id)=(report_drafts.tenant_id,analysis_id)));
CREATE POLICY own_version ON report_draft_versions FOR SELECT TO app_login USING(
 EXISTS(SELECT 1 FROM report_drafts d WHERE (d.tenant_id,d.id)=(report_draft_versions.tenant_id,draft_id)));
CREATE TRIGGER report_version_immutable BEFORE UPDATE OR DELETE ON report_draft_versions
 FOR EACH ROW EXECUTE FUNCTION public.analysis_result_immutable();
CREATE FUNCTION access.report_draft_save(p_id uuid,p_analysis uuid,p_version bigint,p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE original public.analysis_results; task public.analysis_tasks; old public.report_drafts;
 tenant bigint:=public.current_tenant_id(); actor uuid:=public.current_actor_id(); key text; saved jsonb;
BEGIN
 SELECT * INTO original FROM public.analysis_results WHERE id=p_analysis AND tenant_id=tenant AND user_id=actor;
 IF NOT FOUND THEN RAISE EXCEPTION 'report analysis unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO task FROM public.analysis_tasks WHERE id=original.task_id;
 IF NOT access.analysis_visible(task.tenant_id,task.user_id,task.shop_id,task.platform_id,task.account_scope)
 OR NOT access.analysis_shop(task.shop_id,'task.create') THEN
 RAISE EXCEPTION 'report scope denied' USING ERRCODE='42501'; END IF;
 IF octet_length(p_payload::text)>1048576 THEN RAISE EXCEPTION 'report too large' USING ERRCODE='22023'; END IF;
 FOREACH key IN ARRAY ARRAY['query','shopName','platformName','metrics','points','sections','insights','coverage','attribution'] LOOP
  IF p_payload->key IS DISTINCT FROM original.payload->key THEN
   RAISE EXCEPTION 'report evidence changed' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF p_payload->>'schemaVersion' IS DISTINCT FROM 'business-report-v1' OR p_payload->>'source' IS DISTINCT FROM 'API'
 OR p_payload->>'id' IS DISTINCT FROM p_id::text OR p_payload->>'analysisId' IS DISTINCT FROM p_analysis::text
 OR (p_payload->>'version')::bigint IS DISTINCT FROM p_version THEN
 RAISE EXCEPTION 'report identity mismatch' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_id::text,760405));
 SELECT * INTO old FROM public.report_drafts WHERE id=p_id FOR UPDATE;
 IF FOUND THEN
  IF old.tenant_id<>tenant OR old.user_id<>actor OR old.analysis_id<>p_analysis THEN
   RAISE EXCEPTION 'report ownership denied' USING ERRCODE='42501'; END IF;
  IF old.version<>p_version THEN RAISE EXCEPTION 'report version conflict' USING ERRCODE='P0409'; END IF;
  IF old.payload->'period' IS DISTINCT FROM p_payload->'period' THEN
   RAISE EXCEPTION 'report period immutable' USING ERRCODE='22023'; END IF;
 ELSIF p_version<>0 THEN RAISE EXCEPTION 'report first version must be zero' USING ERRCODE='P0409'; END IF;
 saved:=p_payload||jsonb_build_object('version',p_version+1,'updatedAt',clock_timestamp());
 INSERT INTO public.report_drafts(id,tenant_id,user_id,analysis_id,version,payload)
 VALUES(p_id,tenant,actor,p_analysis,p_version+1,saved)
 ON CONFLICT(id) DO UPDATE SET version=excluded.version,payload=excluded.payload;
 INSERT INTO public.report_draft_versions(tenant_id,draft_id,version,payload)
 VALUES(tenant,p_id,p_version+1,saved);
 RETURN saved;
END $$;
REVOKE ALL ON FUNCTION access.report_draft_save(uuid,uuid,bigint,jsonb) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.report_draft_save(uuid,uuid,bigint,jsonb) TO app_login;
