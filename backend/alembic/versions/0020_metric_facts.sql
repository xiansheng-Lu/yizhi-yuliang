-- 按13册先实现事实所需SKU/计划目录外键支撑；不冒充M1-08/11真实镜像交付。
CREATE TABLE skus (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id bigint NOT NULL REFERENCES tenants(id),
 product_id uuid NOT NULL,native_id varchar(128) NOT NULL,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,id,product_id),UNIQUE(tenant_id,product_id,native_id),
 FOREIGN KEY(tenant_id,product_id) REFERENCES products(tenant_id,id)
);
CREATE TABLE campaigns (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id bigint NOT NULL REFERENCES tenants(id),
 shop_id uuid NOT NULL,account_id uuid NOT NULL,provider varchar(20) NOT NULL,
 product_line varchar(30) NOT NULL,native_id varchar(128) NOT NULL,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,id,shop_id,account_id),
 UNIQUE(tenant_id,account_id,provider,product_line,native_id),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id),
 FOREIGN KEY(tenant_id,account_id,provider,product_line) REFERENCES ad_accounts(tenant_id,id,provider,product_line)
);
ALTER TABLE sync_runs ADD CONSTRAINT ux_run_source UNIQUE(tenant_id,source_id,id);
ALTER TABLE sync_pages ADD CONSTRAINT ux_page_proof UNIQUE(tenant_id,run_id,page_no,outbound_id);

-- 初期普通表+日期索引，保留与未来分区兼容的主键；不预造月份事实。
CREATE TABLE metric_facts (
 id bigint GENERATED ALWAYS AS IDENTITY,fact_date date NOT NULL,tenant_id bigint NOT NULL REFERENCES tenants(id),
 granularity varchar(4) NOT NULL CHECK(granularity IN ('DAY','HOUR')),
 fact_hour smallint CHECK(fact_hour BETWEEN 0 AND 23),
 provider varchar(20) NOT NULL,product_line varchar(30) NOT NULL,
 entity_kind varchar(12) NOT NULL CHECK(entity_kind IN ('SHOP','ACCOUNT','CAMPAIGN','LIVE','PRODUCT','SKU','VIDEO')),
 native_entity_id varchar(128) NOT NULL,shop_id uuid NOT NULL,account_id uuid,campaign_id uuid,
 live_id uuid,product_id uuid,sku_id uuid,
 metric_name varchar(40) NOT NULL,unit varchar(8) NOT NULL
  CHECK(unit IN ('MINOR','COUNT','RATE','RATIO','HOURS','BEAN')),
 currency varchar(4) CHECK(currency IN ('CNY','BEAN')),
 timezone varchar(40) NOT NULL CHECK(timezone='Asia/Shanghai'),
 attribution_window varchar(80) NOT NULL,
 refund_policy varchar(20) NOT NULL CHECK(refund_policy IN (
  'GROSS','NET_OF_REFUND','EXCLUDE_REFUND','NOT_APPLICABLE','UNKNOWN')),
 basis_currency varchar(4) CHECK(basis_currency IN ('CNY','BEAN')),
 comparable_key varchar(200) GENERATED ALWAYS AS (
  'v1|'||timezone||'|'||attribution_window||'|'||refund_policy||'|'||coalesce(currency,'-')||'|'||
  unit||'|'||coalesce(basis_currency,'-')) STORED,
 value_num numeric(24,6) NOT NULL,numerator numeric(24,6),denominator numeric(24,6),
 source_updated_at timestamptz,fetched_at timestamptz NOT NULL,
 source_version bigint CHECK(source_version>=0),version_stream varchar(80),
 source_id uuid NOT NULL,sync_run_id uuid NOT NULL,page_no integer NOT NULL,outbound_id uuid NOT NULL,
 confirmed_sequence bigint NOT NULL CHECK(confirmed_sequence>0),revision_number bigint NOT NULL DEFAULT 1,
 PRIMARY KEY(id,fact_date),UNIQUE(tenant_id,id,fact_date),
 CHECK((granularity='DAY' AND fact_hour IS NULL) OR (granularity='HOUR' AND fact_hour IS NOT NULL)),
 CHECK(unit NOT IN ('MINOR','BEAN','COUNT') OR (value_num=trunc(value_num) AND abs(value_num)<=9007199254740991)),
 CHECK((unit='MINOR' AND currency IS NOT DISTINCT FROM 'CNY') OR
  (unit='BEAN' AND currency IS NOT DISTINCT FROM 'BEAN') OR
  (unit NOT IN ('MINOR','BEAN') AND currency IS NULL)),
 CHECK(unit IN ('RATE','RATIO') OR basis_currency IS NULL),
 CHECK((unit IN ('RATE','RATIO') AND numerator IS NOT NULL AND denominator IS NOT NULL
   AND denominator<>0 AND value_num=round(numerator/NULLIF(denominator,0),6)) OR
  (unit NOT IN ('RATE','RATIO') AND numerator IS NULL AND denominator IS NULL)),
 CHECK((source_version IS NULL)=(version_stream IS NULL)),
 CHECK(sku_id IS NULL OR product_id IS NOT NULL),CHECK(campaign_id IS NULL OR account_id IS NOT NULL),
 CHECK(entity_kind<>'CAMPAIGN' OR (campaign_id IS NOT NULL AND account_id IS NOT NULL)),
 CHECK(entity_kind<>'SKU' OR sku_id IS NOT NULL),CHECK(entity_kind<>'PRODUCT' OR product_id IS NOT NULL),
 CHECK(entity_kind<>'LIVE' OR live_id IS NOT NULL),CHECK(entity_kind<>'ACCOUNT' OR account_id IS NOT NULL),
 FOREIGN KEY(tenant_id,shop_id) REFERENCES shops(tenant_id,id),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id),
 FOREIGN KEY(tenant_id,account_id,provider,product_line) REFERENCES ad_accounts(tenant_id,id,provider,product_line),
 FOREIGN KEY(tenant_id,live_id,shop_id) REFERENCES live_sessions(tenant_id,id,shop_id),
 FOREIGN KEY(tenant_id,product_id,shop_id) REFERENCES products(tenant_id,id,shop_id),
 FOREIGN KEY(tenant_id,sku_id,product_id) REFERENCES skus(tenant_id,id,product_id),
 FOREIGN KEY(tenant_id,campaign_id,shop_id,account_id) REFERENCES campaigns(tenant_id,id,shop_id,account_id),
 FOREIGN KEY(tenant_id,source_id,sync_run_id) REFERENCES sync_runs(tenant_id,source_id,id),
 FOREIGN KEY(tenant_id,sync_run_id,page_no,outbound_id) REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
CREATE UNIQUE INDEX ux_metric_facts_biz ON metric_facts(
 tenant_id,fact_date,granularity,fact_hour,provider,product_line,entity_kind,native_entity_id,
 shop_id,account_id,campaign_id,live_id,product_id,sku_id,metric_name,unit,currency,timezone,
 attribution_window,refund_policy,basis_currency,comparable_key) NULLS NOT DISTINCT;
CREATE INDEX ix_metric_scope_date ON metric_facts(tenant_id,shop_id,account_id,fact_date);

CREATE TABLE metric_fact_revisions (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,tenant_id bigint NOT NULL REFERENCES tenants(id),
 fact_id bigint,fact_date date NOT NULL,key_digest varchar(64) NOT NULL,key_json jsonb NOT NULL,
 payload_digest varchar(64) NOT NULL,decision varchar(24) NOT NULL CHECK(decision IN (
  'APPLIED','DUPLICATE','STALE','CONFLICT','MISSING','UNDEFINED_RATIO')),
 value_num numeric(24,6),numerator numeric(24,6),denominator numeric(24,6),
 source_updated_at timestamptz,fetched_at timestamptz NOT NULL,
 source_version bigint,version_stream varchar(80),source_id uuid NOT NULL,
 sync_run_id uuid NOT NULL,page_no integer NOT NULL,outbound_id uuid NOT NULL,
 confirmed_sequence bigint NOT NULL,applied_revision bigint,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,key_digest,outbound_id),
 FOREIGN KEY(tenant_id,fact_id,fact_date) REFERENCES metric_facts(tenant_id,id,fact_date),
 FOREIGN KEY(tenant_id,source_id,sync_run_id) REFERENCES sync_runs(tenant_id,source_id,id),
 FOREIGN KEY(tenant_id,sync_run_id,page_no,outbound_id) REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['skus','campaigns','metric_facts','metric_fact_revisions'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I FOR ALL TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT,INSERT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE POLICY tenant_update ON metric_facts FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
GRANT UPDATE(value_num,numerator,denominator,source_updated_at,fetched_at,source_version,version_stream,
 source_id,sync_run_id,page_no,outbound_id,confirmed_sequence,revision_number) ON metric_facts TO app_login;
GRANT USAGE ON SEQUENCE metric_facts_id_seq,metric_fact_revisions_id_seq TO app_login;
