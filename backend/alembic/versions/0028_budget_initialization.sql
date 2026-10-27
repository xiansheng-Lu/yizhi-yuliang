ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition','risk.assess','budget.initialize'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK(object_type IN (
 'user','shop','member','role','project','conversation','message','job','platform_request',
 'oauth_state','authorization','sync_source','budget_ledger'));
-- 每个账户必须显式核对币种；CNY来源必须是全配置列表（包括暂停），不能拿单对象源顶替。
CREATE TABLE budget_account_contracts (
 tenant_id bigint NOT NULL,account_id uuid NOT NULL,currency varchar(4) NOT NULL CHECK(currency IN ('CNY','BEAN')),
 source_id uuid,authorization_id uuid,source_policy_revision bigint,filter_digest varchar(64),empty_cursor_digest varchar(64),
 budget_field varchar(40),state_field varchar(40),currency_field varchar(40),
 coverage_kind varchar(40) NOT NULL CHECK(coverage_kind='ALL_CONFIGURED_INCLUDING_PAUSED'),
 proof_ref varchar(180) NOT NULL CHECK(proof_ref ~ '^[A-Za-z0-9_.:/-]{1,180}$'),
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),PRIMARY KEY(tenant_id,account_id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 FOREIGN KEY(tenant_id,source_id,account_id) REFERENCES sync_sources(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,authorization_id) REFERENCES provider_authorizations(tenant_id,id),
 CHECK(currency<>'CNY' OR (source_id IS NOT NULL AND source_policy_revision>0
  AND authorization_id IS NOT NULL AND filter_digest ~ '^[0-9a-f]{64}$'
  AND empty_cursor_digest ~ '^[0-9a-f]{64}$' AND budget_field ~ '^[a-z][a-z0-9_]{0,39}$'
  AND state_field ~ '^[a-z][a-z0-9_]{0,39}$' AND currency_field ~ '^[a-z][a-z0-9_]{0,39}$') IS TRUE)
);
CREATE TABLE budget_observations (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,ledger_id uuid NOT NULL,policy_version bigint NOT NULL,
 period_start date NOT NULL,coverage_digest varchar(64) NOT NULL CHECK(coverage_digest ~ '^[0-9a-f]{64}$'),
 confirmed_total_minor bigint NOT NULL CHECK(confirmed_total_minor>=0),
 reserved_minor bigint NOT NULL CHECK(reserved_minor>=0),limit_minor bigint NOT NULL CHECK(limit_minor>0),
 previous_total_minor bigint,delta_minor bigint,freeze_reason varchar(40),
 sources jsonb NOT NULL CHECK(jsonb_typeof(sources)='array'),
 components jsonb NOT NULL CHECK(jsonb_typeof(components)='array'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),request_id uuid NOT NULL,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,ledger_id,policy_version,period_start,coverage_digest),
 FOREIGN KEY(tenant_id,ledger_id) REFERENCES budget_ledgers(tenant_id,id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['budget_account_contracts','budget_observations'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;

CREATE FUNCTION access.budget_initialize(p_member uuid,p_user uuid,p_request uuid,p_expected_policy bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); stamp timestamptz; day date; policy public.risk_policies;
 limit_value bigint; contract record; source record; run public.sync_runs; clocks record; mirror record;
 ledger public.budget_ledgers; observation public.budget_observations;
 total numeric:=0; value numeric; count_rows bigint; all_components jsonb:='[]'; sources jsonb:='[]';
 accounts uuid[]:='{}'; minimum_source timestamptz; minimum_fetch timestamptz;
 valid_until_at timestamptz; anchor timestamptz; payload jsonb; coverage text; identity uuid;
 frozen text; all_source_known boolean:=true;
BEGIN
 IF p_user IS DISTINCT FROM public.current_actor_id() OR p_request IS NULL OR NOT EXISTS(
  SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.tenants t ON t.id=m.tenant_id WHERE m.tenant_id=tenant AND m.id=p_member
   AND m.user_id=p_user AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND t.status='ACTIVE' AND t.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
    WHERE g.tenant_id=tenant AND g.membership_id=m.id AND rp.permission_code='policy.edit')
   AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.tenant_id=tenant AND ds.membership_id=m.id
    AND ds.scope_type='ALL_TENANT' AND (ds.permissions_mask IS NULL OR 'policy.edit'=ANY(ds.permissions_mask)))
 ) THEN RAISE EXCEPTION 'budget maintenance unavailable' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 stamp:=clock_timestamp(); day:=(stamp AT TIME ZONE 'Asia/Shanghai')::date;
 SELECT * INTO policy FROM public.risk_policies WHERE tenant_id=tenant;
 IF NOT FOUND OR policy.version<>p_expected_policy THEN
  RAISE EXCEPTION 'policy changed' USING ERRCODE='40001'; END IF;
 SELECT (config->>'daily_budget_minor')::bigint INTO limit_value FROM public.policy_versions
  WHERE tenant_id=tenant AND policy_id=policy.id AND version=policy.version AND config->>'currency'='CNY';
 IF limit_value IS NULL OR limit_value<=0 THEN RAISE EXCEPTION 'invalid currency/limit' USING ERRCODE='22023'; END IF;
 -- 不按provider猜币种；新增/撤销/删除状态账户也不能悄悄从预算覆盖中消失。
 IF NOT EXISTS(SELECT 1 FROM public.ad_accounts WHERE tenant_id=tenant) OR EXISTS(
  SELECT 1 FROM public.ad_accounts a WHERE a.tenant_id=tenant AND NOT EXISTS(
   SELECT 1 FROM public.budget_account_contracts c WHERE c.tenant_id=tenant AND c.account_id=a.id)) THEN
  RAISE EXCEPTION 'account budget coverage unknown' USING ERRCODE='22023'; END IF;
 FOR contract IN SELECT * FROM public.budget_account_contracts WHERE tenant_id=tenant ORDER BY account_id LOOP
  -- 分类合同revision也进入证明，BEAN只保留独立分类，不加入CNY。
  sources:=sources||jsonb_build_array(jsonb_build_object('accountId',contract.account_id,
   'currency',contract.currency,'contractRevision',contract.revision,'proof',contract.proof_ref));
  IF contract.currency='BEAN' THEN CONTINUE; END IF;
  accounts:=array_append(accounts,contract.account_id);
  PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text||'|'||contract.account_id::text,760117));
  PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text||'|'||contract.account_id::text,760116));
  SELECT s.*,b.active,b.catalog_sha256,p.revision AS policy_revision,
   p.clock_kind,p.target_mode,p.max_age_seconds INTO source
   FROM public.sync_sources s JOIN public.capability_bindings b ON (b.tenant_id,b.id)=(s.tenant_id,s.binding_id)
   JOIN public.resource_freshness_policies p ON (p.tenant_id,p.source_id)=(s.tenant_id,s.id)
   WHERE s.tenant_id=tenant AND s.id=contract.source_id FOR SHARE OF s;
  IF NOT FOUND OR NOT source.enabled OR NOT source.active OR source.target_mode<>'ACCOUNT'
   OR source.filter_digest<>contract.filter_digest OR source.policy_revision<>contract.source_policy_revision
   OR NOT EXISTS(SELECT 1 FROM public.catalog_head WHERE catalog_sha256=source.catalog_sha256)
   OR NOT EXISTS(SELECT 1 FROM public.provider_authorizations a WHERE a.tenant_id=tenant
    AND a.id=contract.authorization_id AND a.status='ACTIVE' AND a.expires_at>stamp) THEN
   RAISE EXCEPTION 'budget source contract not current' USING ERRCODE='22023'; END IF;
  SELECT * INTO run FROM public.sync_runs WHERE tenant_id=tenant AND source_id=contract.source_id
   ORDER BY source_version DESC,started_at DESC LIMIT 1;
  IF NOT FOUND OR run.state<>'SUCCESS' OR run.page_no<1 OR run.items_count>100000 THEN
   RAISE EXCEPTION 'complete budget snapshot unavailable' USING ERRCODE='22023'; END IF;
  SELECT count(*) AS pages,count(source_updated_at) AS known_source,min(source_updated_at) AS source_at,
   min(fetched_at) AS fetched_at,sum(records_count) AS records,
   bool_or(source_updated_at>fetched_at OR fetched_at>stamp) AS skew,
   count(*) FILTER(WHERE terminal AND page_no=run.page_no-1) AS terminal_pages,
   count(*) FILTER(WHERE terminal) AS all_terminal_pages,
   count(*) FILTER(WHERE page_no=0 AND input_cursor_digest=contract.empty_cursor_digest) AS initial_pages
   INTO clocks FROM public.sync_pages WHERE tenant_id=tenant AND run_id=run.id;
  anchor:=CASE WHEN source.clock_kind='SOURCE' THEN clocks.source_at ELSE clocks.fetched_at END;
  IF clocks.pages<>run.page_no OR clocks.records<>run.items_count OR clocks.terminal_pages<>1
   OR clocks.all_terminal_pages<>1 OR clocks.initial_pages<>1
   OR (source.clock_kind='SOURCE' AND clocks.known_source<>clocks.pages)
   OR clocks.skew OR anchor IS NULL OR clocks.fetched_at IS NULL
   OR anchor>stamp OR anchor+make_interval(secs=>source.max_age_seconds)<stamp THEN
   RAISE EXCEPTION 'budget snapshot time/coverage unknown' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.sync_pages p JOIN public.platform_requests r
   ON (r.tenant_id,r.id)=(p.tenant_id,p.outbound_id) WHERE p.tenant_id=tenant AND p.run_id=run.id
   AND (r.authorization_id<>contract.authorization_id OR r.account_id<>contract.account_id
    OR r.binding_id<>source.binding_id OR r.effect<>'READ'
    OR NOT EXISTS(SELECT 1 FROM public.platform_raw_payloads raw WHERE raw.tenant_id=tenant
     AND raw.outbound_id=r.id AND raw.phase='RESPONSE' AND raw.external_outcome='READ_SUCCEEDED'))) THEN
   RAISE EXCEPTION 'budget snapshot receipts/context unknown' USING ERRCODE='22023'; END IF;
  count_rows:=0;
  FOR mirror IN SELECT m.*,p.page_no,p.source_updated_at AS page_source_at,p.fetched_at AS page_fetched_at FROM public.sync_resource_mirrors m
   JOIN public.sync_pages p ON (p.tenant_id,p.outbound_id)=(m.tenant_id,m.outbound_id)
   WHERE m.tenant_id=tenant AND m.source_id=contract.source_id AND p.run_id=run.id ORDER BY m.native_id LOOP
   payload:=mirror.projection->'data'; count_rows:=count_rows+1;
   IF (mirror.projection->>'truncated')::boolean IS DISTINCT FROM false
    OR mirror.source_at IS DISTINCT FROM mirror.page_source_at OR mirror.fetched_at IS DISTINCT FROM mirror.page_fetched_at
    OR jsonb_typeof(payload->contract.budget_field) IS DISTINCT FROM 'number'
    OR payload->>contract.currency_field IS DISTINCT FROM 'CNY'
    OR jsonb_typeof(payload->contract.state_field) IS DISTINCT FROM 'boolean' THEN
    RAISE EXCEPTION 'budget plan fields unknown' USING ERRCODE='22023'; END IF;
   value:=(payload->>contract.budget_field)::numeric;
   IF value<0 OR value<>trunc(value) OR value>9007199254740991 THEN
    RAISE EXCEPTION 'budget unit/precision invalid' USING ERRCODE='22023'; END IF;
   IF NOT EXISTS(SELECT 1 FROM public.platform_raw_payloads r WHERE r.tenant_id=tenant
    AND r.outbound_id=mirror.outbound_id AND r.phase='RESPONSE' AND r.external_outcome='READ_SUCCEEDED') THEN
    RAISE EXCEPTION 'budget read receipt missing' USING ERRCODE='22023'; END IF;
   total:=total+value;
   all_components:=all_components||jsonb_build_array(jsonb_build_object('accountId',contract.account_id,
    'nativeId',mirror.native_id,'budgetMinor',value,'enabled',payload->contract.state_field,
    'sourceId',contract.source_id,'runId',run.id,'outboundId',mirror.outbound_id,'page',mirror.page_no));
  END LOOP;
  IF count_rows<>run.items_count THEN RAISE EXCEPTION 'budget rows omitted or duplicate' USING ERRCODE='22023'; END IF;
  sources:=sources||jsonb_build_array(jsonb_build_object('sourceId',contract.source_id,'runId',run.id,
   'sourceVersion',run.source_version,'items',run.items_count,'sourceAt',clocks.source_at,'fetchedAt',clocks.fetched_at));
  minimum_source:=least(minimum_source,clocks.source_at); minimum_fetch:=least(minimum_fetch,clocks.fetched_at);
  all_source_known:=all_source_known AND clocks.source_at IS NOT NULL;
  valid_until_at:=least(valid_until_at,anchor+make_interval(secs=>source.max_age_seconds));
 END LOOP;
 IF cardinality(accounts)=0 OR total>9007199254740991 THEN
  RAISE EXCEPTION 'no confirmed CNY bucket or aggregate overflow' USING ERRCODE='22023'; END IF;
 -- 额度桶稳定ID跨日期/策略保存R，不能换桶产生新的0预占。
 SELECT * INTO ledger FROM public.budget_ledgers WHERE tenant_id=tenant AND policy_id=policy.id
  AND bucket_key='TENANT_DAILY' AND currency='CNY' FOR UPDATE;
 identity:=coalesce(ledger.id,gen_random_uuid());
 coverage:=encode(sha256(convert_to(jsonb_build_object('sources',sources,'components',all_components)::text,'UTF8')),'hex');
 SELECT * INTO observation FROM public.budget_observations WHERE tenant_id=tenant AND ledger_id=identity
  AND policy_version=policy.version AND period_start=day AND coverage_digest=coverage;
 IF FOUND THEN RETURN jsonb_build_object('ledgerId',identity,'observationId',observation.id,'replayed',true); END IF;
 frozen:=CASE WHEN ledger.freeze_reason IS NOT NULL AND ledger.freeze_reason<>'OVER_LIMIT'
  THEN ledger.freeze_reason WHEN total+coalesce(ledger.reserved_increase_minor,0)>limit_value THEN 'OVER_LIMIT' END;
 INSERT INTO public.budget_ledgers(id,tenant_id,policy_id,policy_version,bucket_key,period_start,period_end,
  currency,limit_minor,confirmed_total_minor,reserved_increase_minor,data_state,coverage_kind,
  covered_account_ids,source_ref,source_updated_at,fetched_at,valid_until,version,freeze_reason)
 VALUES(identity,tenant,policy.id,policy.version,'TENANT_DAILY',day,day+1,'CNY',limit_value,total,
  coalesce(ledger.reserved_increase_minor,0),'CONFIRMED','ALL_CONFIGURED_INCLUDING_PAUSED',accounts,
  'budget-observation/'||coverage,CASE WHEN all_source_known THEN minimum_source END,
  minimum_fetch,valid_until_at,coalesce(ledger.version,0)+1,frozen)
 ON CONFLICT(id) DO UPDATE SET policy_version=EXCLUDED.policy_version,period_start=EXCLUDED.period_start,
  period_end=EXCLUDED.period_end,limit_minor=EXCLUDED.limit_minor,confirmed_total_minor=EXCLUDED.confirmed_total_minor,
  data_state=EXCLUDED.data_state,covered_account_ids=EXCLUDED.covered_account_ids,source_ref=EXCLUDED.source_ref,
  coverage_kind=EXCLUDED.coverage_kind,source_updated_at=EXCLUDED.source_updated_at,fetched_at=EXCLUDED.fetched_at,
  valid_until=EXCLUDED.valid_until,version=EXCLUDED.version,freeze_reason=EXCLUDED.freeze_reason;
 INSERT INTO public.budget_observations(id,tenant_id,ledger_id,policy_version,period_start,coverage_digest,
  confirmed_total_minor,reserved_minor,limit_minor,previous_total_minor,delta_minor,freeze_reason,sources,components,request_id)
 VALUES(gen_random_uuid(),tenant,identity,policy.version,day,coverage,total,coalesce(ledger.reserved_increase_minor,0),
  limit_value,ledger.confirmed_total_minor,total-ledger.confirmed_total_minor,frozen,sources,all_components,p_request)
 RETURNING * INTO observation;
 RETURN jsonb_build_object('ledgerId',identity,'observationId',observation.id,'replayed',false);
END $$;
REVOKE ALL ON FUNCTION access.budget_initialize(uuid,uuid,uuid,bigint) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_initialize(uuid,uuid,uuid,bigint) TO app_login;

-- 已初始化B不是永久资格；账户覆盖、来源合同/批次或授权变化后必须重新聚合。
CREATE FUNCTION access.budget_basis_current(p_ledger uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); ledger public.budget_ledgers; observation public.budget_observations;
BEGIN
 SELECT * INTO ledger FROM public.budget_ledgers WHERE tenant_id=tenant AND id=p_ledger;
 IF NOT FOUND OR ledger.source_ref NOT LIKE 'budget-observation/%' THEN RETURN false; END IF;
 SELECT * INTO observation FROM public.budget_observations WHERE tenant_id=tenant AND ledger_id=p_ledger
  AND period_start=ledger.period_start AND policy_version=ledger.policy_version
  AND 'budget-observation/'||coverage_digest=ledger.source_ref;
 IF NOT FOUND THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.ad_accounts a WHERE a.tenant_id=tenant AND NOT EXISTS(
  SELECT 1 FROM public.budget_account_contracts c WHERE c.tenant_id=tenant AND c.account_id=a.id))
  OR ledger.covered_account_ids IS DISTINCT FROM (SELECT array_agg(account_id ORDER BY account_id)
   FROM public.budget_account_contracts WHERE tenant_id=tenant AND currency='CNY') THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.budget_account_contracts c WHERE c.tenant_id=tenant AND NOT EXISTS(
  SELECT 1 FROM jsonb_array_elements(observation.sources) item
  WHERE item->>'accountId'=c.account_id::text AND item->>'currency'=c.currency
   AND (item->>'contractRevision')::bigint=c.revision AND item->>'proof'=c.proof_ref)) THEN RETURN false; END IF;
 RETURN NOT EXISTS(
  SELECT 1 FROM public.budget_account_contracts c
  LEFT JOIN public.sync_sources s ON (s.tenant_id,s.id)=(c.tenant_id,c.source_id)
  LEFT JOIN public.capability_bindings b ON (b.tenant_id,b.id)=(s.tenant_id,s.binding_id)
  LEFT JOIN public.resource_freshness_policies p ON (p.tenant_id,p.source_id)=(s.tenant_id,s.id)
  LEFT JOIN public.provider_authorizations a ON (a.tenant_id,a.id)=(c.tenant_id,c.authorization_id)
  WHERE c.tenant_id=tenant AND c.currency='CNY' AND (
   s.id IS NULL OR b.id IS NULL OR p.source_id IS NULL OR a.id IS NULL
   OR NOT s.enabled OR NOT b.active OR p.target_mode<>'ACCOUNT' OR p.revision<>c.source_policy_revision
   OR s.filter_digest<>c.filter_digest OR a.status<>'ACTIVE' OR a.expires_at<=clock_timestamp()
   OR NOT EXISTS(SELECT 1 FROM public.catalog_head WHERE catalog_sha256=b.catalog_sha256)
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(observation.sources) item
    JOIN LATERAL(SELECT * FROM public.sync_runs r WHERE r.tenant_id=tenant AND r.source_id=c.source_id
     ORDER BY r.source_version DESC,r.started_at DESC LIMIT 1) latest ON true
    WHERE item->>'sourceId'=c.source_id::text AND item->>'runId'=latest.id::text
     AND latest.state='SUCCESS' AND (item->>'sourceVersion')::bigint=latest.source_version)));
END $$;
REVOKE ALL ON FUNCTION access.budget_basis_current(uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_basis_current(uuid) TO app_login;
