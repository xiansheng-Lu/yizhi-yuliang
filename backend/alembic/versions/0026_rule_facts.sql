-- 先实现规则必需读合同；没有初始化来源时confirmed_total_minor保持NULL。
CREATE TABLE budget_ledgers (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,policy_id uuid NOT NULL,policy_version bigint NOT NULL,
 bucket_key varchar(80) NOT NULL,account_id uuid,
 period_start date NOT NULL,period_end date NOT NULL CHECK(period_end=period_start+1),
 currency varchar(4) NOT NULL CHECK(currency IN ('CNY','BEAN')),
 limit_minor bigint NOT NULL CHECK(limit_minor>0),confirmed_total_minor bigint CHECK(confirmed_total_minor>=0),
 reserved_increase_minor bigint NOT NULL DEFAULT 0 CHECK(reserved_increase_minor>=0),
 data_state varchar(15) NOT NULL DEFAULT 'UNINITIALIZED' CHECK(data_state IN ('UNINITIALIZED','CONFIRMED')),
 coverage_kind varchar(40) NOT NULL DEFAULT 'UNKNOWN'
  CHECK(coverage_kind IN ('UNKNOWN','ALL_CONFIGURED_INCLUDING_PAUSED')),
 covered_account_ids uuid[] NOT NULL DEFAULT '{}',
 source_ref varchar(180),source_updated_at timestamptz,fetched_at timestamptz,valid_until timestamptz,
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),freeze_reason varchar(40),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,policy_id,bucket_key,currency),
 FOREIGN KEY(tenant_id,policy_id,policy_version) REFERENCES policy_versions(tenant_id,policy_id,version),
 FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 CHECK(data_state<>'CONFIRMED' OR (confirmed_total_minor IS NOT NULL
  AND coverage_kind='ALL_CONFIGURED_INCLUDING_PAUSED' AND cardinality(covered_account_ids)>0
  AND source_ref ~ '^[A-Za-z0-9_.:/-]{1,180}$' AND fetched_at IS NOT NULL
  AND valid_until>fetched_at AND (source_updated_at IS NULL OR source_updated_at<=fetched_at)) IS TRUE)
);
-- 不设B+R<=limit：允许外部已超限真值，规则只限制新的增额。
ALTER TABLE platform_raw_payloads ADD CONSTRAINT ux_raw_rule_proof
 UNIQUE(tenant_id,id,outbound_id,phase,external_outcome);
ALTER TABLE command_jobs ADD CONSTRAINT ux_job_rule_account UNIQUE(tenant_id,id,account_id);
ALTER TABLE campaigns ADD CONSTRAINT ux_campaign_rule_account UNIQUE(tenant_id,id,account_id);
ALTER TABLE platform_requests ADD CONSTRAINT ux_request_rule_account UNIQUE(tenant_id,id,account_id);
CREATE TABLE verified_action_facts (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,job_id uuid NOT NULL,account_id uuid NOT NULL,
 campaign_id uuid NOT NULL,provider varchar(20) NOT NULL,product_line varchar(30) NOT NULL,
 action_type varchar(30) NOT NULL CHECK(action_type IN (
  'CHANGE_DAILY_BUDGET','CHANGE_BID','SET_ROI_TARGET','PAUSE_AD_PLAN','RESUME_AD_PLAN')),
 write_outbound_id uuid NOT NULL UNIQUE,read_outbound_id uuid NOT NULL UNIQUE,
 write_payload_id bigint NOT NULL,read_payload_id bigint NOT NULL,
 write_phase varchar(10) NOT NULL DEFAULT 'RESPONSE' CHECK(write_phase='RESPONSE'),
 read_phase varchar(10) NOT NULL DEFAULT 'RESPONSE' CHECK(read_phase='RESPONSE'),
 write_outcome varchar(20) NOT NULL DEFAULT 'ACCEPTED' CHECK(write_outcome='ACCEPTED'),
 read_outcome varchar(20) NOT NULL DEFAULT 'READ_SUCCEEDED' CHECK(read_outcome='READ_SUCCEEDED'),
 observed_value jsonb NOT NULL CHECK(jsonb_typeof(observed_value)='object'),
 verified_at timestamptz NOT NULL,proof_ref varchar(180) NOT NULL
  CHECK(proof_ref ~ '^[A-Za-z0-9_.:/-]{1,180}$'),
 FOREIGN KEY(tenant_id,job_id,account_id) REFERENCES command_jobs(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,campaign_id,account_id) REFERENCES campaigns(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,write_outbound_id,account_id) REFERENCES platform_requests(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,read_outbound_id,account_id) REFERENCES platform_requests(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,account_id,provider,product_line) REFERENCES ad_accounts(tenant_id,id,provider,product_line),
 FOREIGN KEY(tenant_id,write_payload_id,write_outbound_id,write_phase,write_outcome)
  REFERENCES platform_raw_payloads(tenant_id,id,outbound_id,phase,external_outcome),
 FOREIGN KEY(tenant_id,read_payload_id,read_outbound_id,read_phase,read_outcome)
  REFERENCES platform_raw_payloads(tenant_id,id,outbound_id,phase,external_outcome),
 CHECK(write_outbound_id<>read_outbound_id)
);
CREATE INDEX ix_verified_action_day ON verified_action_facts(tenant_id,account_id,verified_at);
CREATE INDEX ix_verified_action_target ON verified_action_facts(tenant_id,campaign_id,verified_at DESC);
CREATE INDEX ix_rule_pending_writes ON platform_requests(tenant_id,account_id,created_at) WHERE effect='WRITE';
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['budget_ledgers','verified_action_facts'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;

DROP POLICY tenant_read ON verified_action_facts;
CREATE POLICY job_private ON verified_action_facts FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND EXISTS(
  SELECT 1 FROM command_jobs j WHERE j.tenant_id=verified_action_facts.tenant_id
   AND j.id=verified_action_facts.job_id));

-- 只返回精确授权账户的计数/时间；其他发起人的原文RLS仍不开放。
CREATE FUNCTION access.risk_action_summary(p_member uuid,p_user uuid,p_job uuid,p_shop uuid,
 p_account uuid,p_target uuid,p_stage text,p_at timestamptz) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); permission text; since_at timestamptz;
 count_today bigint; last_at timestamptz; unresolved bigint;
BEGIN
 permission:=CASE WHEN p_stage='APPROVE' THEN 'task.approve' ELSE 'task.create' END;
 IF p_stage NOT IN ('CREATE','CONFIRM','APPROVE','EXECUTE') OR p_at IS NULL OR p_at>clock_timestamp()
  OR p_user IS DISTINCT FROM public.current_actor_id() OR NOT EXISTS(
  SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  WHERE m.tenant_id=tenant AND m.id=p_member AND m.user_id=p_user AND m.status='ACTIVE'
   AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM public.tenants t WHERE t.id=tenant AND t.status='ACTIVE' AND t.deleted_at IS NULL)
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
    WHERE g.tenant_id=tenant AND g.membership_id=p_member AND rp.permission_code=permission)
   AND EXISTS(SELECT 1 FROM public.shops s JOIN public.data_scopes ds ON ds.tenant_id=s.tenant_id
    WHERE s.tenant_id=tenant AND s.id=p_shop AND s.deleted_at IS NULL AND ds.membership_id=p_member
     AND (ds.permissions_mask IS NULL OR permission=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id))
   AND EXISTS(SELECT 1 FROM public.ad_accounts a JOIN public.data_scopes ds ON ds.tenant_id=a.tenant_id
    WHERE a.tenant_id=tenant AND a.id=p_account AND a.deleted_at IS NULL AND ds.membership_id=p_member
     AND (ds.permissions_mask IS NULL OR permission=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.account_id=a.id OR ds.entity_id=a.entity_id))
 ) OR NOT EXISTS(SELECT 1 FROM public.campaigns c WHERE c.tenant_id=tenant AND c.id=p_target
  AND c.shop_id=p_shop AND c.account_id=p_account) THEN
  RAISE EXCEPTION 'risk window unavailable' USING ERRCODE='42501'; END IF;
 IF p_stage<>'CREATE' THEN
  PERFORM access.job_state_context(p_member,p_user,p_job,
   CASE p_stage WHEN 'APPROVE' THEN 'APPROVE' WHEN 'EXECUTE' THEN 'CLAIM' ELSE 'CONFIRM' END);
  IF NOT EXISTS(SELECT 1 FROM public.command_jobs j WHERE j.tenant_id=tenant AND j.id=p_job
   AND j.account_id=p_account AND j.shop_id=p_shop AND j.target_id=p_target) THEN
   RAISE EXCEPTION 'risk window context mismatch' USING ERRCODE='42501'; END IF;
 END IF;
 since_at:=date_trunc('day',p_at AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai';
 SELECT count(*) INTO count_today FROM public.verified_action_facts
  WHERE tenant_id=tenant AND account_id=p_account AND verified_at>=since_at
   AND verified_at<since_at+interval '1 day';
 SELECT max(verified_at) INTO last_at FROM public.verified_action_facts
  WHERE tenant_id=tenant AND campaign_id=p_target;
 SELECT count(*) INTO unresolved FROM public.platform_requests r
  WHERE r.tenant_id=tenant AND r.account_id=p_account AND r.effect='WRITE'
   AND NOT EXISTS(SELECT 1 FROM public.verified_action_facts f WHERE f.tenant_id=tenant AND f.write_outbound_id=r.id)
   AND NOT EXISTS(SELECT 1 FROM public.platform_raw_payloads x WHERE x.tenant_id=tenant AND x.outbound_id=r.id
    AND x.phase IN ('RESPONSE','ERROR') AND x.external_outcome IN ('REJECTED','NOT_SENT'));
 RETURN jsonb_build_object('verifiedToday',count_today,'lastVerifiedAt',last_at,
   'unresolvedWrites',unresolved,'dayStart',since_at);
END $$;
REVOKE ALL ON FUNCTION access.risk_action_summary(uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.risk_action_summary(uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz) TO app_login;
