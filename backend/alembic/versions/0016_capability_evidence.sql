ALTER TABLE endpoint_catalog ADD CONSTRAINT ux_endpoint_provider
 UNIQUE(catalog_sha256,catalog_id,provider);
CREATE TABLE capability_bindings (
 id uuid PRIMARY KEY, tenant_id bigint NOT NULL REFERENCES tenants(id),
 account_id uuid NOT NULL, connector_id varchar(20) NOT NULL REFERENCES catalog_connectors(connector_id),
 provider varchar(20) NOT NULL, product_line varchar(30) NOT NULL,
 action varchar(20) NOT NULL CHECK(action IN ('create','budget','bid','pause','resume','review','analyze','goal','allocate','verify','retry','rules')),
 operation_kind varchar(5) NOT NULL CHECK(operation_kind IN ('READ','WRITE')),
 catalog_sha256 varchar(64) NOT NULL, catalog_id varchar(20) NOT NULL,
 revision integer NOT NULL CHECK(revision>0),
 http_method varchar(7) NOT NULL CHECK(http_method IN ('GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS')),
 normalized_path text NOT NULL CHECK(normalized_path ~ '^/[^[:space:]?#]*$'),
 permission_code varchar(80) NOT NULL REFERENCES permissions(code),
 active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,account_id,provider,product_line)
  REFERENCES ad_accounts(tenant_id,id,provider,product_line),
 FOREIGN KEY(catalog_sha256,catalog_id,provider)
  REFERENCES endpoint_catalog(catalog_sha256,catalog_id,provider),
 CHECK((connector_id,provider,product_line) IN (
  ('adq','tencent','adq'),('wxpromo','tencent','shop'),('beans','beans','beans'),
  ('wxshop','wxshop','wxshop'),('qianchuan','ocean','qianchuan'),
  ('douyinshop','ocean','douyinshop'),('juguang','xhs','juguang'),('redshop','xhs','redshop'))),
 CHECK(operation_kind<>'WRITE' OR http_method NOT IN ('GET','HEAD','OPTIONS')),
 CHECK(operation_kind<>'READ' OR http_method IN ('GET','HEAD','OPTIONS','POST')),
 CHECK(operation_kind<>'WRITE' OR permission_code=CASE action
  WHEN 'create' THEN 'plan.write' WHEN 'budget' THEN 'budget.edit' WHEN 'bid' THEN 'plan.write'
  WHEN 'pause' THEN 'plan.write' WHEN 'resume' THEN 'plan.write' WHEN 'goal' THEN 'goal.edit'
  WHEN 'allocate' THEN 'budget.edit' WHEN 'rules' THEN 'policy.edit' WHEN 'review' THEN 'task.approve'
  ELSE 'task.create' END)
);
CREATE UNIQUE INDEX ux_active_capability_binding ON capability_bindings
 (tenant_id,account_id,connector_id,product_line,action,operation_kind,catalog_sha256,catalog_id)
 WHERE active;
CREATE TABLE capability_assertions (
 id uuid PRIMARY KEY, sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 tenant_id bigint NOT NULL, binding_id uuid NOT NULL,
 phase varchar(24) NOT NULL CHECK(phase IN ('PATH_VERIFIED','SCOPE_GRANTED','ACCOUNT_AUTHORIZED','BACKEND_INTEGRATED','SCHEMA_VERIFIED','EXECUTION_VERIFIED')),
 result varchar(7) NOT NULL CHECK(result IN ('PASS','FAIL','UNKNOWN')),
 proof_kind varchar(24) NOT NULL,
 proof_ref varchar(256) NOT NULL CHECK(proof_ref ~ '^[A-Za-z0-9][A-Za-z0-9_./:-]{0,255}$'),
 checked_by varchar(120) NOT NULL CHECK(length(checked_by)>0),
 checked_at timestamptz NOT NULL, expires_at timestamptz NOT NULL CHECK(expires_at>checked_at),
 recorded_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,binding_id) REFERENCES capability_bindings(tenant_id,id),
 CHECK((phase,proof_kind) IN (
  ('PATH_VERIFIED','DOCUMENTATION'),('SCOPE_GRANTED','SCOPE_GRANT'),
  ('ACCOUNT_AUTHORIZED','ACCOUNT_AUTH'),('BACKEND_INTEGRATED','ADAPTER_INTEGRATION'),
  ('SCHEMA_VERIFIED','READ_SCHEMA'),('SCHEMA_VERIFIED','WRITE_SCHEMA'),
  ('EXECUTION_VERIFIED','READ_RESULT'),('EXECUTION_VERIFIED','MATCHED_RECEIPT')))
);
CREATE INDEX ix_capability_latest ON capability_assertions
 (tenant_id,binding_id,phase,checked_at DESC,sequence DESC);
CREATE TABLE capability_validation_permits (
 id uuid PRIMARY KEY, tenant_id bigint NOT NULL, binding_id uuid NOT NULL,
 requester_id uuid NOT NULL, approved_by uuid NOT NULL,
 approval_ref varchar(256) NOT NULL CHECK(approval_ref ~ '^[A-Za-z0-9][A-Za-z0-9_./:-]{0,255}$'),
 request_key uuid NOT NULL, request_digest varchar(64) NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
 max_amount_minor bigint NOT NULL CHECK(max_amount_minor BETWEEN 0 AND 9007199254740991),
 starts_at timestamptz NOT NULL, expires_at timestamptz NOT NULL CHECK(expires_at>starts_at),
 used_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,request_key),
 FOREIGN KEY(tenant_id,binding_id) REFERENCES capability_bindings(tenant_id,id),
 FOREIGN KEY(tenant_id,requester_id) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,approved_by) REFERENCES memberships(tenant_id,user_id),
 CHECK(requester_id<>approved_by)
);
CREATE UNIQUE INDEX ux_one_bootstrap_attempt ON capability_validation_permits(tenant_id,binding_id)
 WHERE used_at IS NOT NULL;
CREATE FUNCTION public.capability_binding_immutable() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 IF to_jsonb(NEW)-'active' IS DISTINCT FROM to_jsonb(OLD)-'active' THEN
  RAISE EXCEPTION 'capability context is immutable; create a new binding';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.capability_binding_immutable() FROM PUBLIC;
CREATE TRIGGER capability_binding_immutable BEFORE UPDATE ON capability_bindings
 FOR EACH ROW EXECUTE FUNCTION public.capability_binding_immutable();
CREATE FUNCTION public.invalidate_changed_account_capabilities() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 IF (NEW.status,NEW.deleted_at,NEW.native_id) IS DISTINCT FROM (OLD.status,OLD.deleted_at,OLD.native_id) THEN
  UPDATE public.capability_bindings SET active=false WHERE tenant_id=OLD.tenant_id AND account_id=OLD.id AND active;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.invalidate_changed_account_capabilities() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER account_capabilities_changed AFTER UPDATE OF status,deleted_at,native_id ON ad_accounts
 FOR EACH ROW EXECUTE FUNCTION public.invalidate_changed_account_capabilities();
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['capability_bindings','capability_assertions','capability_validation_permits'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY capability_maintenance ON public.%I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY capability_read ON public.%I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,app_login,app_auth',item);
  EXECUTE format('GRANT SELECT ON public.%I TO app_login',item);
 END LOOP;
END $$;
REVOKE ALL ON SEQUENCE capability_assertions_sequence_seq FROM PUBLIC,app_login,app_auth;

CREATE FUNCTION access.consume_capability_validation(
 p_permit uuid,p_shop uuid,p_key uuid,p_digest text,p_amount bigint
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER STRICT
 SET search_path=pg_catalog,pg_temp AS $$
DECLARE permit public.capability_validation_permits%ROWTYPE;
 binding public.capability_bindings%ROWTYPE;
 account public.ad_accounts%ROWTYPE;
 actor uuid:=public.current_actor_id(); tenant bigint:=public.current_tenant_id();
 stamp timestamptz:=clock_timestamp(); affected integer;
BEGIN
 IF tenant IS NULL OR actor IS NULL OR p_amount<0 OR p_amount>9007199254740991 THEN RETURN false; END IF;
 SELECT p.* INTO permit FROM public.capability_validation_permits p
  WHERE p.id=p_permit AND p.tenant_id=tenant FOR UPDATE;
 IF NOT FOUND OR permit.used_at IS NOT NULL OR permit.requester_id<>actor
  OR permit.request_key<>p_key OR permit.request_digest<>p_digest
  OR p_amount>permit.max_amount_minor OR NOT (permit.starts_at<=stamp AND stamp<permit.expires_at)
 THEN RETURN false; END IF;
 SELECT b.* INTO binding FROM public.capability_bindings b
  WHERE b.id=permit.binding_id AND b.tenant_id=tenant FOR UPDATE;
 IF NOT FOUND OR NOT binding.active OR binding.operation_kind<>'WRITE'
  OR binding.provider<>'tencent' OR binding.connector_id NOT IN ('adq','wxpromo') THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.capability_validation_permits p WHERE p.tenant_id=tenant
   AND p.binding_id=binding.id AND p.used_at IS NOT NULL) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.catalog_head h WHERE h.catalog_sha256=binding.catalog_sha256) THEN RETURN false; END IF;
 SELECT a.* INTO account FROM public.ad_accounts a
  WHERE a.id=binding.account_id AND a.tenant_id=tenant FOR UPDATE;
 IF NOT FOUND OR account.deleted_at IS NOT NULL OR account.status<>'AUTHORIZED' THEN RETURN false; END IF;
 IF NOT EXISTS (
  SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
   JOIN public.role_grants g
   ON (g.tenant_id,g.membership_id)=(m.tenant_id,m.id)
   JOIN public.role_permissions rp ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
   WHERE m.tenant_id=tenant AND m.user_id=permit.approved_by AND m.status='ACTIVE'
    AND u.status='ACTIVE' AND u.deleted_at IS NULL
    AND rp.permission_code='task.approve'
    AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.tenant_id=tenant
     AND ds.membership_id=m.id AND (ds.permissions_mask IS NULL OR 'task.approve'=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.account_id=account.id OR ds.entity_id=account.entity_id))
    AND EXISTS(SELECT 1 FROM public.shops s JOIN public.data_scopes ds ON ds.tenant_id=s.tenant_id
     WHERE s.tenant_id=tenant AND s.id=p_shop AND s.deleted_at IS NULL AND ds.membership_id=m.id
      AND (ds.permissions_mask IS NULL OR 'task.approve'=ANY(ds.permissions_mask))
      AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id))
 ) THEN RETURN false; END IF;
 IF NOT EXISTS (
  SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
   JOIN public.role_grants g
   ON (g.tenant_id,g.membership_id)=(m.tenant_id,m.id)
   JOIN public.role_permissions rp ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
   WHERE m.tenant_id=tenant AND m.user_id=actor AND m.status='ACTIVE'
    AND u.status='ACTIVE' AND u.deleted_at IS NULL
    AND rp.permission_code=binding.permission_code
    AND EXISTS(SELECT 1 FROM public.entity_links l JOIN public.shops s
      ON (s.tenant_id,s.id)=(l.tenant_id,l.shop_id)
      WHERE l.tenant_id=tenant AND l.shop_id=p_shop AND l.account_id=binding.account_id
       AND s.deleted_at IS NULL
       AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.tenant_id=tenant
        AND ds.membership_id=m.id AND (ds.permissions_mask IS NULL OR binding.permission_code=ANY(ds.permissions_mask))
        AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id))
       AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.tenant_id=tenant
        AND ds.membership_id=m.id AND (ds.permissions_mask IS NULL OR binding.permission_code=ANY(ds.permissions_mask))
        AND (ds.scope_type='ALL_TENANT' OR ds.account_id=account.id OR ds.entity_id=account.entity_id)))
 ) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.capability_assertions a WHERE a.tenant_id=tenant
   AND a.binding_id=binding.id AND a.phase='EXECUTION_VERIFIED') THEN RETURN false; END IF;
 IF (WITH latest AS (
  SELECT DISTINCT ON(a.phase) a.* FROM public.capability_assertions a
   WHERE a.tenant_id=tenant AND a.binding_id=binding.id
   ORDER BY a.phase,a.checked_at DESC,a.sequence DESC
 ), ordered AS (SELECT a.*,array_position(ARRAY['PATH_VERIFIED','SCOPE_GRANTED','ACCOUNT_AUTHORIZED',
   'BACKEND_INTEGRATED','SCHEMA_VERIFIED'],a.phase::text) AS ordinal FROM latest a)
 SELECT count(*)=5 AND NOT EXISTS(SELECT 1 FROM ordered earlier JOIN ordered later
   ON later.ordinal>earlier.ordinal WHERE (later.checked_at,later.sequence)<(earlier.checked_at,earlier.sequence))
 FROM latest a WHERE a.result='PASS' AND a.checked_at<=stamp AND stamp<a.expires_at
   AND (a.phase<>'SCHEMA_VERIFIED' OR a.proof_kind='WRITE_SCHEMA')) IS NOT TRUE THEN RETURN false; END IF;
 UPDATE public.capability_validation_permits SET used_at=stamp
  WHERE id=permit.id AND tenant_id=tenant AND used_at IS NULL;
 GET DIAGNOSTICS affected=ROW_COUNT;
 RETURN affected=1;
END $$;
REVOKE ALL ON FUNCTION access.consume_capability_validation(uuid,uuid,uuid,text,bigint) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.consume_capability_validation(uuid,uuid,uuid,text,bigint) TO app_login;
