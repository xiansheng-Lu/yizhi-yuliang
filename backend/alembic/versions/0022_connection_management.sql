ALTER TABLE provider_authorizations ADD CONSTRAINT ux_authorization_context
 UNIQUE(tenant_id,id,provider,product_line);
CREATE TABLE authorization_account_links (
 tenant_id bigint NOT NULL,authorization_id uuid NOT NULL,account_id uuid NOT NULL,
 provider varchar(20) NOT NULL,product_line varchar(20) NOT NULL,
 proof_ref varchar(180) NOT NULL CHECK(proof_ref ~ '^[A-Za-z0-9_.:/-]{1,180}$'),
 verified_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,authorization_id,account_id),
 FOREIGN KEY(tenant_id,authorization_id,provider,product_line)
  REFERENCES provider_authorizations(tenant_id,id,provider,product_line),
 FOREIGN KEY(tenant_id,account_id,provider,product_line)
  REFERENCES ad_accounts(tenant_id,id,provider,product_line)
);
CREATE TABLE connection_disconnections (
 tenant_id bigint NOT NULL,authorization_id uuid NOT NULL,
 actor_id uuid NOT NULL REFERENCES users(id),request_id uuid NOT NULL,
 preview_digest varchar(64) NOT NULL CHECK(preview_digest ~ '^[0-9a-f]{64}$'),
 impact jsonb NOT NULL CHECK(jsonb_typeof(impact)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,authorization_id),
 FOREIGN KEY(tenant_id,authorization_id) REFERENCES provider_authorizations(tenant_id,id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['authorization_account_links','connection_disconnections'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE INDEX ix_platform_connection ON platform_requests(tenant_id,authorization_id,account_id);
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect'));

-- 精确原会话+ALL_TENANT管理授权；只返回受控对象ID/数量，不放开私有请求原文RLS。
-- 所有新平台意图/同步领取先持有相同账户共享锁；断开独占至事务提交。
CREATE FUNCTION access.connection_impact(p_member uuid,p_user uuid,p_session uuid,p_id uuid,
 p_apply boolean,p_expected jsonb,p_digest text,p_request uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); grant_row public.provider_authorizations;
 accounts uuid[]; bindings uuid[]; sources uuid[]; pending uuid[]; runs uuid[]; jobs uuid[];
 account uuid; result jsonb; previous jsonb; history_count bigint;
BEGIN
 IF p_user IS DISTINCT FROM public.current_actor_id()
  OR NOT access.oauth_actor(tenant,p_member,p_user,p_session) THEN
  RAISE EXCEPTION 'connection permission denied' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_id::text,760118));
 SELECT * INTO grant_row FROM public.provider_authorizations WHERE tenant_id=tenant AND id=p_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'connection unavailable' USING ERRCODE='P0002'; END IF;
 SELECT coalesce(array_agg(id ORDER BY id),'{}'::uuid[]) INTO accounts FROM (
  SELECT account_id AS id FROM public.authorization_account_links
   WHERE tenant_id=tenant AND authorization_id=p_id
  UNION SELECT grant_row.account_id WHERE grant_row.account_id IS NOT NULL) a;
 FOREACH account IN ARRAY accounts LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text||'|'||account::text,760117));
 END LOOP;
 SELECT * INTO grant_row FROM public.provider_authorizations
  WHERE tenant_id=tenant AND id=p_id FOR UPDATE;
 SELECT coalesce(array_agg(id ORDER BY id),'{}'::uuid[]) INTO bindings
  FROM public.capability_bindings WHERE tenant_id=tenant AND account_id=ANY(accounts)
   AND connector_id=grant_row.connector_id AND provider=grant_row.provider
   AND product_line=grant_row.product_line AND active;
 SELECT coalesce(array_agg(s.id ORDER BY s.id),'{}'::uuid[]) INTO sources
  FROM public.sync_sources s JOIN public.capability_bindings b
   ON (b.tenant_id,b.id)=(s.tenant_id,s.binding_id)
  WHERE s.tenant_id=tenant AND s.account_id=ANY(accounts) AND s.enabled
   AND b.connector_id=grant_row.connector_id AND b.product_line=grant_row.product_line;
 SELECT coalesce(array_agg(id ORDER BY id),'{}'::uuid[]) INTO runs FROM public.sync_runs
  WHERE tenant_id=tenant AND source_id=ANY(sources) AND state='RUNNING';
 SELECT coalesce(array_agg(id ORDER BY id),'{}'::uuid[]) INTO jobs FROM public.command_jobs
  WHERE tenant_id=tenant AND account_id=ANY(accounts);
 SELECT coalesce(array_agg(id ORDER BY id),'{}'::uuid[]) INTO pending FROM (
  SELECT r.id FROM public.platform_requests r WHERE r.tenant_id=tenant
   AND (r.authorization_id=p_id OR (r.account_id=ANY(accounts)
    AND r.connector_id=grant_row.connector_id AND r.product_line=grant_row.product_line))
   AND (NOT EXISTS(SELECT 1 FROM public.platform_raw_payloads x
      WHERE x.tenant_id=tenant AND x.outbound_id=r.id AND x.phase IN ('RESPONSE','ERROR'))
    OR EXISTS(SELECT 1 FROM public.platform_raw_payloads x
      WHERE x.tenant_id=tenant AND x.outbound_id=r.id
       AND (x.external_outcome='UNKNOWN' OR (r.effect='WRITE' AND x.external_outcome='ACCEPTED'))))
  ORDER BY r.id LIMIT 1001) unresolved;
 IF greatest(cardinality(accounts),cardinality(bindings),cardinality(sources),
  cardinality(runs),cardinality(jobs),cardinality(pending))>1000 THEN
  RAISE EXCEPTION 'connection impact too large' USING ERRCODE='54000'; END IF;
 SELECT count(*) INTO history_count FROM public.platform_requests r WHERE r.tenant_id=tenant
  AND (r.authorization_id=p_id OR (r.account_id=ANY(accounts)
   AND r.connector_id=grant_row.connector_id AND r.product_line=grant_row.product_line));
 result:=jsonb_build_object('authorizationId',p_id,'authorizationVersion',grant_row.version,
  'authorizationStatus',grant_row.status,'accountIds',accounts,'bindingIds',bindings,
  'sourceIds',sources,'runningSyncIds',runs,'jobIds',jobs,'inFlightReadbackIds',pending,
  'preservedRequestCount',history_count,'providerRevocation','NOT_REQUESTED');
 IF NOT p_apply THEN RETURN result; END IF;
 SELECT impact INTO previous FROM public.connection_disconnections
  WHERE tenant_id=tenant AND authorization_id=p_id AND preview_digest=p_digest;
 IF FOUND THEN RETURN previous; END IF;
 IF result IS DISTINCT FROM p_expected OR grant_row.status='REVOKED'
  OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$' OR p_request IS NULL THEN
  RAISE EXCEPTION 'connection impact changed' USING ERRCODE='40001'; END IF;
 UPDATE public.provider_authorizations SET status='REVOKED',version=version+1
  WHERE tenant_id=tenant AND id=p_id;
 UPDATE public.capability_bindings SET active=false WHERE tenant_id=tenant AND id=ANY(bindings);
 UPDATE public.sync_sources SET enabled=false WHERE tenant_id=tenant AND id=ANY(sources);
 INSERT INTO public.connection_disconnections(tenant_id,authorization_id,actor_id,request_id,preview_digest,impact)
  VALUES(tenant,p_id,p_user,p_request,p_digest,result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION access.connection_impact(uuid,uuid,uuid,uuid,boolean,jsonb,text,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.connection_impact(uuid,uuid,uuid,uuid,boolean,jsonb,text,uuid) TO app_login;
