CREATE TABLE content_document_asset_versions (
 tenant_id bigint NOT NULL,document_id uuid NOT NULL,revision integer NOT NULL,asset_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,document_id,revision,asset_id),
 FOREIGN KEY(tenant_id,document_id,revision) REFERENCES content_document_versions(tenant_id,document_id,revision),
 FOREIGN KEY(tenant_id,asset_id) REFERENCES content_assets(tenant_id,id)
);
CREATE TABLE content_asset_references (
 tenant_id bigint NOT NULL,asset_id uuid NOT NULL,
 kind varchar(12) NOT NULL CHECK(kind IN ('GENERATION','SOP','DELIVERY')),
 reference_id uuid NOT NULL,active boolean NOT NULL DEFAULT true,
 PRIMARY KEY(tenant_id,asset_id,kind,reference_id),
 FOREIGN KEY(tenant_id,asset_id) REFERENCES content_assets(tenant_id,id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['content_document_asset_versions','content_asset_references'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT,INSERT ON %I TO app_login',item);
 END LOOP;
 CREATE POLICY tenant_update ON content_asset_references FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
 GRANT UPDATE ON content_asset_references TO app_login;
 CREATE POLICY tenant_archive ON content_assets FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
 GRANT UPDATE(deleted_at) ON content_assets TO app_login;
END $$;
ALTER TABLE commerce_products ADD COLUMN deleted_at timestamptz;
CREATE FUNCTION guard_content_asset_reference() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE asset content_assets%ROWTYPE; doc_shop uuid; doc_product text; active_reference boolean:=true;
BEGIN
 IF TG_OP='UPDATE' THEN
  IF (OLD.tenant_id,OLD.asset_id,OLD.kind,OLD.reference_id) IS DISTINCT FROM
   (NEW.tenant_id,NEW.asset_id,NEW.kind,NEW.reference_id) THEN
   RAISE EXCEPTION 'reference identity is immutable' USING ERRCODE='23514';
  END IF;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text||'|'||NEW.asset_id::text,760159));
 SELECT a.* INTO asset FROM content_assets a JOIN content_uploads u
  ON (a.tenant_id,a.id)=(u.tenant_id,u.id)
  WHERE a.tenant_id=NEW.tenant_id AND a.id=NEW.asset_id AND u.state='CONFIRMED';
 IF NOT FOUND THEN RAISE EXCEPTION 'confirmed asset required' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='content_asset_references' THEN active_reference:=NEW.active; END IF;
 IF active_reference THEN
  IF asset.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'archived asset cannot be referenced' USING ERRCODE='23514'; END IF;
  IF asset.kind NOT IN ('image','text') OR asset.duration_state<>'NOT_APPLICABLE' THEN
   RAISE EXCEPTION 'unverified asset type' USING ERRCODE='23514';
  END IF;
  IF asset.product_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM commerce_products p WHERE p.tenant_id=asset.tenant_id AND p.id=asset.product_id AND p.deleted_at IS NULL
  ) THEN RAISE EXCEPTION 'removed product cannot be referenced' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_TABLE_NAME='content_document_asset_versions' THEN
  SELECT d.shop_id,v.payload->>'productId' INTO doc_shop,doc_product
   FROM content_document_versions v JOIN content_documents d
    ON (d.tenant_id,d.id)=(v.tenant_id,v.document_id)
   WHERE v.tenant_id=NEW.tenant_id AND v.document_id=NEW.document_id AND v.revision=NEW.revision;
  IF NOT FOUND OR doc_shop<>asset.shop_id OR
     (asset.product_id IS NOT NULL AND asset.product_id::text IS DISTINCT FROM COALESCE(doc_product,'')) THEN
   RAISE EXCEPTION 'document asset scope mismatch' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER asset_document_reference_guard BEFORE INSERT ON content_document_asset_versions
 FOR EACH ROW EXECUTE FUNCTION guard_content_asset_reference();
CREATE TRIGGER asset_inflight_reference_guard BEFORE INSERT OR UPDATE ON content_asset_references
 FOR EACH ROW EXECUTE FUNCTION guard_content_asset_reference();
CREATE FUNCTION guard_content_asset_archive() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text||'|'||NEW.id::text,760159));
 IF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL THEN
  RAISE EXCEPTION 'asset archive cannot be reversed by runtime' USING ERRCODE='23514';
 END IF;
 IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL AND (
  EXISTS(SELECT 1 FROM content_document_asset_versions WHERE tenant_id=NEW.tenant_id AND asset_id=NEW.id) OR
  EXISTS(SELECT 1 FROM content_asset_references WHERE tenant_id=NEW.tenant_id AND asset_id=NEW.id AND active)
 ) THEN RAISE EXCEPTION 'referenced asset cannot be archived' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER asset_archive_guard BEFORE UPDATE OF deleted_at ON content_assets
 FOR EACH ROW EXECUTE FUNCTION guard_content_asset_archive();
REVOKE ALL ON FUNCTION guard_content_asset_reference(),guard_content_asset_archive() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION guard_content_asset_reference(),guard_content_asset_archive() TO app_login,app_owner;
