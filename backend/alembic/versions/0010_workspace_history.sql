CREATE FUNCTION public.current_actor_id() RETURNS uuid
LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 RETURN nullif(current_setting('app.current_actor',true),'')::uuid;
EXCEPTION WHEN invalid_text_representation THEN RETURN NULL;
END; $$;
REVOKE ALL ON FUNCTION public.current_actor_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_actor_id() TO app_login;

CREATE FUNCTION access.workspace_allowed(p_member uuid,p_user uuid) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.tenants t ON t.id=m.tenant_id
  JOIN public.role_grants g ON (g.tenant_id,g.membership_id)=(m.tenant_id,m.id)
  JOIN public.role_permissions p ON (p.tenant_id,p.role_id)=(g.tenant_id,g.role_id)
  WHERE m.id=p_member AND m.user_id=p_user AND p_user=public.current_actor_id()
   AND m.tenant_id=public.current_tenant_id() AND m.status='ACTIVE'
   AND u.status='ACTIVE' AND u.deleted_at IS NULL AND t.status='ACTIVE' AND t.deleted_at IS NULL
   AND p.permission_code='task.create');
$$;
REVOKE ALL ON FUNCTION access.workspace_allowed(uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.workspace_allowed(uuid,uuid) TO app_login;

CREATE TABLE work_projects (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id bigint NOT NULL REFERENCES tenants(id),user_id uuid NOT NULL,
 name varchar(40) NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 40),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,user_id,id), UNIQUE(tenant_id,user_id,name),
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TABLE conversations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id bigint NOT NULL REFERENCES tenants(id),user_id uuid NOT NULL,
 project_id uuid,title varchar(100) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,user_id,id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,user_id,project_id) REFERENCES work_projects(tenant_id,user_id,id)
);
-- 仅任务标识/归属和对象FK底座，M2-05a/b扩展意图、风险与状态机；当前无创建/执行任务API。
CREATE TABLE command_jobs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id bigint NOT NULL REFERENCES tenants(id),created_by uuid NOT NULL,
 shop_id uuid NOT NULL,account_id uuid,live_session_id uuid,
 status varchar(20) NOT NULL DEFAULT 'DRAFT' CHECK(status='DRAFT'),
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,created_by,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,shop_id) REFERENCES shops(tenant_id,id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 FOREIGN KEY(tenant_id,live_session_id) REFERENCES live_sessions(tenant_id,id),
 FOREIGN KEY(tenant_id,shop_id,account_id) REFERENCES entity_links(tenant_id,shop_id,account_id)
);
CREATE TABLE conversation_messages (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 position bigint GENERATED ALWAYS AS IDENTITY,
 tenant_id bigint NOT NULL REFERENCES tenants(id),user_id uuid NOT NULL,
 conversation_id uuid NOT NULL,
 role varchar(10) NOT NULL CHECK(role IN ('user','assistant')),
 text varchar(1000) NOT NULL,
 interpretation jsonb,scope jsonb,job_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,user_id,conversation_id) REFERENCES conversations(tenant_id,user_id,id),
 FOREIGN KEY(tenant_id,user_id,job_id) REFERENCES command_jobs(tenant_id,created_by,id),
 CHECK((role='user' AND interpretation IS NULL AND scope IS NULL AND job_id IS NULL) OR
       (role='assistant' AND interpretation IS NOT NULL AND scope IS NOT NULL))
);
CREATE UNIQUE INDEX ux_message_job ON conversation_messages(tenant_id,user_id,job_id) WHERE job_id IS NOT NULL;
CREATE TABLE conversation_turns (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 tenant_id bigint NOT NULL REFERENCES tenants(id),user_id uuid NOT NULL,
 client_key varchar(64) NOT NULL,payload_hash varchar(64) NOT NULL,
 response jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,user_id,client_key),
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TABLE task_project_links (
 tenant_id bigint NOT NULL REFERENCES tenants(id),user_id uuid NOT NULL,
 job_id uuid NOT NULL,project_id uuid,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,user_id,job_id),
 FOREIGN KEY(tenant_id,user_id,job_id) REFERENCES command_jobs(tenant_id,created_by,id),
 FOREIGN KEY(tenant_id,user_id,project_id) REFERENCES work_projects(tenant_id,user_id,id)
);
DO $$ DECLARE target text; actor text; BEGIN
 FOREACH target IN ARRAY ARRAY['work_projects','conversations','command_jobs',
   'conversation_messages','conversation_turns','task_project_links'] LOOP
  actor:=CASE WHEN target='command_jobs' THEN 'created_by' ELSE 'user_id' END;
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',target);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',target);
  EXECUTE format('CREATE POLICY workspace_owner ON public.%I TO app_owner USING(true) WITH CHECK(true)',target);
  EXECUTE format('CREATE POLICY workspace_private ON public.%I TO app_login USING(tenant_id=public.current_tenant_id() AND %I=public.current_actor_id()) WITH CHECK(tenant_id=public.current_tenant_id() AND %I=public.current_actor_id())',target,actor,actor);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,app_login,app_auth',target);
  EXECUTE format('GRANT SELECT ON public.%I TO app_login',target);
 END LOOP;
END; $$;
GRANT INSERT ON work_projects,conversations,conversation_messages,conversation_turns,task_project_links TO app_login;
GRANT UPDATE(project_id,updated_at) ON conversations TO app_login;
GRANT UPDATE(job_id) ON conversation_messages TO app_login;
GRANT UPDATE(project_id,updated_at),DELETE ON task_project_links TO app_login;
GRANT USAGE ON SEQUENCE conversation_turns_id_seq TO app_login;
GRANT USAGE ON SEQUENCE conversation_messages_position_seq TO app_login;
CREATE INDEX ix_conversation_owner ON conversations(tenant_id,user_id,updated_at DESC);
CREATE INDEX ix_message_conversation ON conversation_messages(conversation_id,position);
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check
 CHECK(object_type IN ('user','shop','member','role','project','conversation','message','job'));
