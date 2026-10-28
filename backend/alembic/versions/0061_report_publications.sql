CREATE TABLE report_publications (
 id uuid PRIMARY KEY, tenant_id bigint NOT NULL,user_id uuid NOT NULL,draft_id uuid NOT NULL,
 draft_version bigint NOT NULL, idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 200),
 token_hash text NOT NULL UNIQUE CHECK(token_hash ~ '^[a-f0-9]{64}$'),
 nonce text NOT NULL CHECK(nonce ~ '^[a-f0-9]{64}$'), public_origin text NOT NULL,
 snapshot jsonb NOT NULL, published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL, revoked_at timestamptz,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,user_id,idempotency_key),
 FOREIGN KEY(tenant_id,draft_id,draft_version) REFERENCES report_draft_versions(tenant_id,draft_id,version),
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id),
 CHECK(expires_at>published_at)
);
ALTER TABLE report_publications ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_publications FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON report_publications TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY own_read ON report_publications FOR SELECT TO app_login USING(
 tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id()
 AND EXISTS(SELECT 1 FROM report_drafts d WHERE (d.tenant_id,d.id)=(report_publications.tenant_id,draft_id)));
GRANT SELECT ON report_publications TO app_login;
CREATE FUNCTION public.report_publication_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-'revoked_at') IS DISTINCT FROM (to_jsonb(OLD)-'revoked_at')
 OR OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL THEN
 RAISE EXCEPTION 'published report immutable' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER report_publication_immutable BEFORE UPDATE OR DELETE ON report_publications
 FOR EACH ROW EXECUTE FUNCTION public.report_publication_immutable();

CREATE FUNCTION access.report_publish(p_id uuid,p_draft uuid,p_version bigint,p_key text,
 p_hash text,p_nonce text,p_origin text,p_ttl integer) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE d public.report_drafts; t public.analysis_tasks; old public.report_publications;
 tenant bigint:=public.current_tenant_id(); actor uuid:=public.current_actor_id(); now_at timestamptz;
BEGIN
 IF p_id IS NULL OR p_draft IS NULL OR p_version IS NULL OR p_version<1 OR p_key IS NULL
 OR length(p_key) NOT BETWEEN 1 AND 200 OR p_hash IS NULL OR p_hash !~ '^[a-f0-9]{64}$'
 OR p_nonce IS NULL OR p_nonce !~ '^[a-f0-9]{64}$' OR p_origin IS NULL
 OR p_origin !~ '^https?://[^/?#@[:space:]]+$' OR p_ttl IS NULL OR p_ttl NOT BETWEEN 1 AND 31536000 THEN
 RAISE EXCEPTION 'publication input invalid' USING ERRCODE='22023'; END IF;
 SELECT * INTO d FROM public.report_drafts WHERE id=p_draft AND tenant_id=tenant AND user_id=actor FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'publication scope denied' USING ERRCODE='42501'; END IF;
 SELECT task.* INTO t FROM public.analysis_results r JOIN public.analysis_tasks task ON task.id=r.task_id
 WHERE r.id=d.analysis_id AND r.tenant_id=tenant;
 IF NOT FOUND OR NOT access.analysis_visible(t.tenant_id,t.user_id,t.shop_id,t.platform_id,t.account_scope)
 OR NOT access.analysis_shop(t.shop_id,'task.create') THEN
 RAISE EXCEPTION 'publication scope denied' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text||':'||actor::text||':'||p_key,760406));
 SELECT * INTO old FROM public.report_publications WHERE tenant_id=tenant AND user_id=actor AND idempotency_key=p_key;
 IF FOUND THEN
  IF old.draft_id IS DISTINCT FROM p_draft OR old.draft_version IS DISTINCT FROM p_version THEN
   RAISE EXCEPTION 'publication idempotency mismatch' USING ERRCODE='P0410'; END IF;
  RETURN old.id;
 END IF;
 IF d.version IS DISTINCT FROM p_version THEN
 RAISE EXCEPTION 'publication draft version conflict' USING ERRCODE='P0409'; END IF;
 now_at:=clock_timestamp();
 INSERT INTO public.report_publications(id,tenant_id,user_id,draft_id,draft_version,idempotency_key,
 token_hash,nonce,public_origin,snapshot,published_at,expires_at)
 VALUES(p_id,tenant,actor,p_draft,p_version,p_key,p_hash,p_nonce,p_origin,d.payload,now_at,
 now_at+make_interval(secs=>p_ttl));
 RETURN p_id;
END $$;

CREATE FUNCTION access.report_revoke(p_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item public.report_publications; t public.analysis_tasks;
BEGIN
 SELECT * INTO item FROM public.report_publications WHERE id=p_id
 AND tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id() FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT task.* INTO t FROM public.analysis_results r JOIN public.analysis_tasks task ON task.id=r.task_id
 WHERE r.id=(item.snapshot->>'analysisId')::uuid AND r.tenant_id=item.tenant_id;
 IF NOT FOUND OR NOT access.analysis_visible(t.tenant_id,t.user_id,t.shop_id,t.platform_id,t.account_scope)
 OR NOT access.analysis_shop(t.shop_id,'task.create') THEN
 RAISE EXCEPTION 'publication scope denied' USING ERRCODE='42501'; END IF;
 IF item.revoked_at IS NULL THEN UPDATE public.report_publications SET revoked_at=clock_timestamp() WHERE id=p_id; END IF;
 RETURN true;
END $$;

-- 无会话入口只接受高熵能力摘要和该报告ID，不允许枚举或重用到其他对象。
CREATE FUNCTION access.report_public_read(p_id uuid,p_hash text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT snapshot FROM public.report_publications WHERE id=p_id AND token_hash=p_hash
 AND revoked_at IS NULL AND expires_at>clock_timestamp()
 AND p_hash ~ '^[a-f0-9]{64}$'
$$;
REVOKE ALL ON FUNCTION access.report_publish(uuid,uuid,bigint,text,text,text,text,integer),
 access.report_revoke(uuid),access.report_public_read(uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.report_publish(uuid,uuid,bigint,text,text,text,text,integer),
 access.report_revoke(uuid),access.report_public_read(uuid,text) TO app_login;
