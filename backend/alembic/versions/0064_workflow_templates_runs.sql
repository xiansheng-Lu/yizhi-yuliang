CREATE TABLE workflow_templates (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,
 owner_tenant_id bigint GENERATED ALWAYS AS (CASE WHEN tenant_id=0 THEN NULL ELSE tenant_id END) STORED,
 code varchar(40) NOT NULL,version integer NOT NULL CHECK(version>0),name varchar(80) NOT NULL,
 is_system boolean NOT NULL,definition jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((tenant_id=0 AND is_system) OR (tenant_id>0 AND NOT is_system)),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,code,version),
 FOREIGN KEY(owner_tenant_id) REFERENCES tenants(id)
);
CREATE TABLE workflow_runs (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,shop_id uuid NOT NULL,product_id uuid NOT NULL,
 template_tenant_id bigint NOT NULL,template_id uuid NOT NULL,template_version integer NOT NULL,
 root_document_id uuid NOT NULL,creation_key varchar(128) NOT NULL,request_hash text NOT NULL,
 status text NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING','COMPLETED','ABANDONED')),
 created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,creation_key),
 CHECK(template_tenant_id=0 OR template_tenant_id=tenant_id),
 FOREIGN KEY(template_tenant_id,template_id) REFERENCES workflow_templates(tenant_id,id),
 FOREIGN KEY(tenant_id,shop_id) REFERENCES shops(tenant_id,id),
 FOREIGN KEY(tenant_id,product_id,shop_id) REFERENCES commerce_products(tenant_id,id,shop_id),
 FOREIGN KEY(tenant_id,root_document_id) REFERENCES content_documents(tenant_id,id)
);
CREATE TABLE workflow_run_steps (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,run_id uuid NOT NULL,step_code varchar(40) NOT NULL,
 seq integer NOT NULL,status text NOT NULL CHECK(status IN ('PENDING','READY','RUNNING','WAITING_REVIEW','DONE','SKIPPED','FAILED')),
 definition jsonb NOT NULL,UNIQUE(tenant_id,run_id,step_code),
 FOREIGN KEY(tenant_id,run_id) REFERENCES workflow_runs(tenant_id,id)
);
ALTER TABLE content_documents ADD COLUMN workflow_run_id uuid;
ALTER TABLE content_documents ADD CONSTRAINT content_document_workflow_fk
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id);
ALTER TABLE workflow_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_templates FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON workflow_templates TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY template_read ON workflow_templates FOR SELECT TO app_login
 USING(public.current_tenant_id()>0 AND (tenant_id=0 OR tenant_id=public.current_tenant_id()));
CREATE POLICY template_insert ON workflow_templates FOR INSERT TO app_login
 WITH CHECK(tenant_id=public.current_tenant_id() AND tenant_id>0 AND NOT is_system);
GRANT SELECT,INSERT ON workflow_templates TO app_login;
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['workflow_runs','workflow_run_steps'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT,INSERT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE FUNCTION workflow_template_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'workflow templates are append only' USING ERRCODE='23514'; END $$;
CREATE TRIGGER workflow_template_immutable BEFORE UPDATE OR DELETE ON workflow_templates
 FOR EACH ROW EXECUTE FUNCTION workflow_template_immutable();
ALTER TABLE workflow_templates ADD CONSTRAINT workflow_template_version_key UNIQUE(tenant_id,id,version);
ALTER TABLE workflow_runs ADD CONSTRAINT workflow_run_template_version_fk
 FOREIGN KEY(template_tenant_id,template_id,template_version) REFERENCES workflow_templates(tenant_id,id,version);
ALTER TABLE workflow_runs ADD CONSTRAINT workflow_run_product_key UNIQUE(tenant_id,id,shop_id,product_id);
ALTER TABLE content_documents ADD CONSTRAINT content_workflow_scope_key UNIQUE(tenant_id,id,shop_id,product_id);
ALTER TABLE workflow_runs ADD CONSTRAINT workflow_root_scope_fk FOREIGN KEY(tenant_id,root_document_id,shop_id,product_id)
 REFERENCES content_documents(tenant_id,id,shop_id,product_id);
ALTER TABLE content_documents ADD CONSTRAINT content_workflow_scope_fk FOREIGN KEY(tenant_id,workflow_run_id,shop_id,product_id)
 REFERENCES workflow_runs(tenant_id,id,shop_id,product_id);

WITH seeds(id,code,name,codes,kinds,doc_kind) AS (VALUES
 ('00000000-0000-4000-8000-000000000064'::uuid,'planning_script','策划与脚本',ARRAY['brief','topic','planning','script','storyboard'],ARRAY['MANUAL','AI_ASSISTED','AI_ASSISTED','AI_ASSISTED','AI_ASSISTED'],'scripts'),
 ('00000000-0000-4000-8000-000000000065'::uuid,'graphic_post','图文',ARRAY['brief','copy','board','image_generation','review'],ARRAY['MANUAL','AI_ASSISTED','MANUAL','GENERATION','REVIEW'],'graphics'),
 ('00000000-0000-4000-8000-000000000066'::uuid,'product_video','视频',ARRAY['reference','direction','generate','result'],ARRAY['MANUAL','MANUAL','GENERATION','REVIEW'],'videos'),
 ('00000000-0000-4000-8000-000000000067'::uuid,'smart_editing','智能剪辑',ARRAY['source','edit','review'],ARRAY['MANUAL','MANUAL','REVIEW'],'editing'),
 ('00000000-0000-4000-8000-000000000068'::uuid,'asset_review','素材审核',ARRAY['source','review'],ARRAY['MANUAL','REVIEW'],'planning')
)
INSERT INTO workflow_templates(id,tenant_id,code,version,name,is_system,definition)
SELECT id,0,code,1,name,true,jsonb_build_object('code',code,'name',name,'productLines','[]'::jsonb,'steps',
 (SELECT jsonb_agg(jsonb_build_object('code',codes[i],'name',codes[i],'kind',kinds[i],
 'dependsOn',CASE WHEN i=1 THEN '[]'::jsonb ELSE jsonb_build_array(codes[i-1]) END,
 'requiresHumanConfirm',true,'inputs','[]'::jsonb,'documentKind',
 CASE WHEN codes[i] IN ('brief','topic','planning') THEN 'planning' ELSE doc_kind END) ORDER BY i)
 FROM generate_subscripts(codes,1) AS i)) FROM seeds;
