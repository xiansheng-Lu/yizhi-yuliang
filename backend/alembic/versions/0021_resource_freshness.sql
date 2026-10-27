ALTER TABLE sync_pages ADD COLUMN source_updated_at timestamptz;
ALTER TABLE sync_pages ADD COLUMN fetched_at timestamptz;
-- 旧页只取已经保存的真实抓取时间，来源时间保持未知，不拿迁移/查询时间补值。
UPDATE sync_pages p SET fetched_at=coalesce(
 (SELECT min(m.fetched_at) FROM sync_resource_mirrors m
  WHERE m.tenant_id=p.tenant_id AND m.outbound_id=p.outbound_id),
 (SELECT min(r.fetched_at) FROM metric_fact_revisions r
  WHERE r.tenant_id=p.tenant_id AND r.outbound_id=p.outbound_id));
ALTER TABLE sync_page_intents ADD COLUMN last_phase varchar(12) NOT NULL DEFAULT 'PREPARED';
ALTER TABLE sync_page_intents ADD COLUMN last_category varchar(40);
ALTER TABLE sync_page_intents ADD COLUMN last_dispatch varchar(12);
ALTER TABLE sync_page_intents ADD COLUMN last_observed_at timestamptz;
ALTER TABLE sync_page_intents ADD CONSTRAINT ck_intent_observation CHECK(
 last_phase IN ('PREPARED','ATTEMPT','RESPONSE','ERROR') AND
 (last_dispatch IS NULL OR last_dispatch IN ('SENDING','NOT_SENT','UNKNOWN','RESPONDED')) AND
 (last_category IS NULL OR last_category IN ('ACCEPTED','READ_SUCCEEDED','PREFLIGHT_ERROR','TIMEOUT',
  'UPSTREAM_ERROR','RESPONSE_TOO_LARGE','SIGNING_ERROR','INVALID_RESPONSE','UNCONFIRMED',
  'REDIRECT_REFUSED','RATE_LIMITED','PARAMETER_REJECTED','AUTH_REQUIRED','FORBIDDEN','PLATFORM_REJECTED')));
UPDATE sync_page_intents i SET last_phase=raw.phase,last_dispatch=raw.dispatch,
 last_category=CASE WHEN raw.category IS NULL THEN NULL WHEN raw.category IN (
  'ACCEPTED','READ_SUCCEEDED','PREFLIGHT_ERROR','TIMEOUT','UPSTREAM_ERROR','RESPONSE_TOO_LARGE',
  'SIGNING_ERROR','INVALID_RESPONSE','UNCONFIRMED','REDIRECT_REFUSED','RATE_LIMITED',
  'PARAMETER_REJECTED','AUTH_REQUIRED','FORBIDDEN','PLATFORM_REJECTED') THEN raw.category ELSE 'UNCONFIRMED' END,
 last_observed_at=raw.created_at FROM
 (SELECT DISTINCT ON(tenant_id,outbound_id) tenant_id,outbound_id,phase,dispatch,category,created_at
  FROM platform_raw_payloads ORDER BY tenant_id,outbound_id,id DESC) raw
 WHERE (i.tenant_id,i.outbound_id)=(raw.tenant_id,raw.outbound_id);
CREATE POLICY intent_observation_update ON sync_page_intents FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id() AND EXISTS(
   SELECT 1 FROM platform_requests p WHERE p.tenant_id=sync_page_intents.tenant_id
    AND p.id=sync_page_intents.outbound_id))
 WITH CHECK(tenant_id=public.current_tenant_id() AND EXISTS(
   SELECT 1 FROM platform_requests p WHERE p.tenant_id=sync_page_intents.tenant_id
    AND p.id=sync_page_intents.outbound_id));
GRANT UPDATE(last_phase,last_category,last_dispatch,last_observed_at) ON sync_page_intents TO app_login;

ALTER TABLE sync_sources ADD CONSTRAINT ux_source_account UNIQUE(tenant_id,id,account_id);
CREATE TABLE resource_freshness_policies (
 tenant_id bigint NOT NULL REFERENCES tenants(id),source_id uuid NOT NULL,account_id uuid NOT NULL,
 resource_key varchar(60) NOT NULL,max_age_seconds integer NOT NULL CHECK(max_age_seconds BETWEEN 1 AND 2592000),
 refresh_seconds integer NOT NULL CHECK(refresh_seconds BETWEEN 1 AND 2592000),
 clock_kind varchar(8) NOT NULL CHECK(clock_kind IN ('SOURCE','FETCHED')),
 target_mode varchar(8) NOT NULL CHECK(target_mode IN ('ACCOUNT','OBJECT')),
 required_fields jsonb NOT NULL CHECK(jsonb_typeof(required_fields)='array'),
 required_metrics jsonb NOT NULL CHECK(jsonb_typeof(required_metrics)='array'),
 revision integer NOT NULL CHECK(revision>0),proof_ref varchar(180) NOT NULL,
 PRIMARY KEY(tenant_id,source_id),UNIQUE(tenant_id,source_id,resource_key,account_id),
 CHECK(refresh_seconds<=max_age_seconds),
 FOREIGN KEY(tenant_id,source_id,account_id) REFERENCES sync_sources(tenant_id,id,account_id)
);
CREATE TABLE write_resource_requirements (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id bigint NOT NULL REFERENCES tenants(id),
 account_id uuid NOT NULL,write_binding_id uuid NOT NULL,source_id uuid NOT NULL,
 resource_key varchar(60) NOT NULL,target_native_id varchar(128),policy_revision integer NOT NULL,
 proof_ref varchar(180) NOT NULL,
 FOREIGN KEY(tenant_id,write_binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,source_id,resource_key,account_id)
  REFERENCES resource_freshness_policies(tenant_id,source_id,resource_key,account_id)
);
CREATE UNIQUE INDEX ux_write_resource_scope ON write_resource_requirements(
 tenant_id,write_binding_id,resource_key,target_native_id) NULLS NOT DISTINCT;
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['resource_freshness_policies','write_resource_requirements'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I FOR ALL TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;
