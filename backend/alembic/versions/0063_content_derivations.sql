CREATE TABLE content_derivations (
 tenant_id bigint NOT NULL,actor_id uuid NOT NULL,request_key varchar(200) NOT NULL,
 parent_id uuid NOT NULL,parent_revision integer NOT NULL,child_id uuid NOT NULL,
 child_revision integer NOT NULL CHECK(child_revision=1),
 kind varchar(10) NOT NULL CHECK(kind IN ('planning','scripts','graphics','videos','editing')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,actor_id,request_key),UNIQUE(tenant_id,child_id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,parent_id,parent_revision)
 REFERENCES content_document_versions(tenant_id,document_id,revision),
 FOREIGN KEY(tenant_id,child_id,child_revision)
 REFERENCES content_document_versions(tenant_id,document_id,revision),
 CHECK(parent_id<>child_id)
);
ALTER TABLE content_derivations ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_derivations FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON content_derivations TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY self_read ON content_derivations FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
CREATE POLICY self_insert ON content_derivations FOR INSERT TO app_login
 WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
GRANT SELECT,INSERT ON content_derivations TO app_login;
CREATE TRIGGER content_derivations_immutable BEFORE UPDATE OR DELETE ON content_derivations
 FOR EACH ROW EXECUTE FUNCTION public.analysis_result_immutable();
CREATE FUNCTION public.content_derivation_link_valid() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE parent jsonb;child jsonb;
BEGIN
 SELECT payload INTO parent FROM public.content_document_versions
 WHERE (tenant_id,document_id,revision)=(NEW.tenant_id,NEW.parent_id,NEW.parent_revision);
 SELECT payload INTO child FROM public.content_document_versions
 WHERE (tenant_id,document_id,revision)=(NEW.tenant_id,NEW.child_id,NEW.child_revision);
 IF parent IS NULL OR child IS NULL OR child->>'parentId' IS DISTINCT FROM NEW.parent_id::text
 OR child->>'id' IS DISTINCT FROM NEW.child_id::text OR child->>'kind' IS DISTINCT FROM NEW.kind
 OR parent->>'productId' IS DISTINCT FROM child->>'productId' THEN
 RAISE EXCEPTION 'derived content identity mismatch' USING ERRCODE='22023';END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER content_derivation_link_valid BEFORE INSERT ON content_derivations
 FOR EACH ROW EXECUTE FUNCTION public.content_derivation_link_valid();
