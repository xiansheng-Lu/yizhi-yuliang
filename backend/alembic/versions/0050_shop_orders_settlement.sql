CREATE TABLE shop_order_observations (
 tenant_id bigint NOT NULL,run_id uuid NOT NULL,page_no integer NOT NULL,row_no integer NOT NULL,outbound_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('ORDER','SETTLEMENT','ORDER_LIST','AFTERSALE','AFTERSALE_LIST')),native_id text NOT NULL,
 values jsonb NOT NULL CHECK(jsonb_typeof(values)='object'),fetched_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,run_id,page_no,row_no),
 FOREIGN KEY(tenant_id,run_id,page_no,outbound_id) REFERENCES sync_pages(tenant_id,run_id,page_no,outbound_id)
);
ALTER TABLE shop_order_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE shop_order_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON shop_order_observations TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON shop_order_observations FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id());
CREATE POLICY tenant_insert ON shop_order_observations FOR INSERT TO app_login
 WITH CHECK(tenant_id=public.current_tenant_id());
GRANT SELECT,INSERT ON shop_order_observations TO app_login;

CREATE TABLE shop_settlement_proofs (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,account_id uuid NOT NULL,shop_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),binding_id uuid NOT NULL, currency text NOT NULL CHECK(currency='CNY'),
 source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 checked_at timestamptz NOT NULL,unit text NOT NULL CHECK(unit IN ('CNY_MINOR','CNY_YUAN_INTEGER')),
 proof_ref text NOT NULL CHECK(length(proof_ref) BETWEEN 1 AND 180),expires_at timestamptz NOT NULL,active boolean NOT NULL DEFAULT true,
 FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 FOREIGN KEY(tenant_id,binding_id,account_id) REFERENCES capability_bindings(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,shop_id) REFERENCES shops(tenant_id,id)
);
ALTER TABLE shop_settlement_proofs ENABLE ROW LEVEL SECURITY;
ALTER TABLE shop_settlement_proofs FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON shop_settlement_proofs TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON shop_settlement_proofs FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id());
GRANT SELECT ON shop_settlement_proofs TO app_login;

CREATE UNIQUE INDEX shop_settlement_current ON shop_settlement_proofs(tenant_id,account_id,shop_id) WHERE active;
