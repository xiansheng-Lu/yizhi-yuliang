CREATE TABLE adq_asset_pages (
 tenant_id bigint NOT NULL,source_id uuid NOT NULL,run_id uuid NOT NULL,page_no integer NOT NULL,
 outbound_id uuid NOT NULL,kind varchar(12) NOT NULL CHECK(kind IN ('ACCOUNT','CAMPAIGN')),
 wire_page integer NOT NULL CHECK(wire_page>0),page_size integer NOT NULL CHECK(page_size BETWEEN 1 AND 100),
 total_number bigint NOT NULL CHECK(total_number>=0),total_page integer NOT NULL CHECK(total_page>=0),
 PRIMARY KEY(tenant_id,run_id,page_no),
 FOREIGN KEY(tenant_id,source_id,run_id) REFERENCES sync_runs(tenant_id,source_id,id),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id)
 REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
CREATE TABLE adq_asset_mirrors (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,account_id uuid NOT NULL,
 kind varchar(12) NOT NULL CHECK(kind IN ('ACCOUNT','CAMPAIGN')),native_id varchar(128) NOT NULL,
 protocol_version varchar(8) NOT NULL CHECK(protocol_version IN ('1.3','3.0')),
 config jsonb NOT NULL CHECK(jsonb_typeof(config)='object'),config_digest varchar(64) NOT NULL,
 version bigint NOT NULL CHECK(version>0),source_shop_id uuid NOT NULL,
 source_id uuid NOT NULL,run_id uuid NOT NULL,page_no integer NOT NULL,outbound_id uuid NOT NULL,
 fetched_at timestamptz NOT NULL,source_updated_at timestamptz,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,account_id,kind,native_id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 FOREIGN KEY(tenant_id,source_shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id),
 FOREIGN KEY(tenant_id,source_id,run_id) REFERENCES sync_runs(tenant_id,source_id,id),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id)
 REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
CREATE TABLE adq_asset_observations (
 tenant_id bigint NOT NULL,asset_id uuid NOT NULL,run_id uuid NOT NULL,page_no integer NOT NULL,
 outbound_id uuid NOT NULL,applied boolean NOT NULL,fetched_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,run_id,asset_id),
 FOREIGN KEY(tenant_id,asset_id) REFERENCES adq_asset_mirrors(tenant_id,id),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id)
 REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
CREATE TABLE adq_asset_versions (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,tenant_id bigint NOT NULL,
 asset_id uuid NOT NULL,version bigint NOT NULL CHECK(version>0),before_json jsonb,after_json jsonb NOT NULL,
 change_source varchar(16) NOT NULL CHECK(change_source='PLATFORM_READ'),
 run_id uuid NOT NULL,page_no integer NOT NULL,outbound_id uuid NOT NULL,fetched_at timestamptz NOT NULL,
 UNIQUE(tenant_id,asset_id,version),
 FOREIGN KEY(tenant_id,asset_id) REFERENCES adq_asset_mirrors(tenant_id,id),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id)
 REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['adq_asset_pages','adq_asset_mirrors','adq_asset_observations','adq_asset_versions'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT,INSERT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE POLICY tenant_update ON adq_asset_mirrors FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
GRANT UPDATE(config,config_digest,version,source_shop_id,source_id,run_id,page_no,outbound_id,
 fetched_at,source_updated_at) ON adq_asset_mirrors TO app_login;
GRANT USAGE ON SEQUENCE adq_asset_versions_id_seq TO app_login;

-- 账户发现不能证明计划属于某店铺；关联由独立证据维护，不自动借用拉取店铺。
ALTER TABLE campaigns ADD COLUMN kind varchar(20)
 CHECK(kind IN ('CAMPAIGN','ADGROUP','AD','PROMOTION_ORDER'));
ALTER TABLE campaigns ADD COLUMN protocol_version varchar(8);
CREATE TABLE adq_campaign_shop_links (
 tenant_id bigint NOT NULL,asset_id uuid NOT NULL,account_id uuid NOT NULL,shop_id uuid NOT NULL,
 campaign_id uuid NOT NULL,proof_ref varchar(180) NOT NULL,checked_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,CHECK(expires_at>checked_at),
 PRIMARY KEY(tenant_id,asset_id),UNIQUE(tenant_id,campaign_id),
 FOREIGN KEY(tenant_id,asset_id) REFERENCES adq_asset_mirrors(tenant_id,id),
 FOREIGN KEY(tenant_id,campaign_id,shop_id,account_id)
 REFERENCES campaigns(tenant_id,id,shop_id,account_id)
);
ALTER TABLE adq_campaign_shop_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE adq_campaign_shop_links FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON adq_campaign_shop_links TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON adq_campaign_shop_links FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id());
GRANT SELECT ON adq_campaign_shop_links TO app_login;
CREATE FUNCTION public.reject_unverified_legacy_campaign_write() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM public.campaigns c WHERE c.tenant_id=NEW.tenant_id
  AND c.account_id=NEW.account_id AND c.native_id=NEW.target_native_id
  AND c.kind='CAMPAIGN' AND c.protocol_version='1.3') THEN
  RAISE EXCEPTION 'legacy campaign write protocol unverified' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.reject_unverified_legacy_campaign_write() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER reject_unverified_legacy_campaign_write BEFORE INSERT ON execution_orders
 FOR EACH ROW EXECUTE FUNCTION public.reject_unverified_legacy_campaign_write();
