CREATE TABLE resource_query_cursor_owners (
 tenant_id bigint NOT NULL,membership_id uuid NOT NULL,user_id uuid NOT NULL,
 generation bigint NOT NULL CHECK(generation>0),touched_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,membership_id,user_id),
 FOREIGN KEY(tenant_id,membership_id,user_id) REFERENCES memberships(tenant_id,id,user_id)
);
CREATE TABLE resource_query_continuations (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,membership_id uuid NOT NULL,user_id uuid NOT NULL,chain_id uuid NOT NULL,
 source_id uuid NOT NULL,run_id uuid NOT NULL,request_hash varchar(64) NOT NULL,snapshot_hash varchar(64) NOT NULL,
 total_rows integer NOT NULL CHECK(total_rows BETWEEN 101 AND 10000),
 after_offset integer NOT NULL CHECK(after_offset>0 AND after_offset<total_rows AND after_offset%100=0),
 next_id uuid,expires_at timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,membership_id,user_id,chain_id,id),
 UNIQUE(tenant_id,chain_id,after_offset),
 FOREIGN KEY(tenant_id,membership_id,user_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,source_id,run_id) REFERENCES sync_runs(tenant_id,source_id,id),
 FOREIGN KEY(tenant_id,membership_id,user_id,chain_id,next_id)
 REFERENCES resource_query_continuations(tenant_id,membership_id,user_id,chain_id,id)
 DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX resource_query_cursor_cleanup ON resource_query_continuations(tenant_id,membership_id,user_id,expires_at);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['resource_query_cursor_owners','resource_query_continuations'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY private_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id())',item);
  EXECUTE format('CREATE POLICY private_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id())',item);
  EXECUTE format('GRANT SELECT,INSERT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE POLICY private_update ON resource_query_cursor_owners FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id())
 WITH CHECK(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id());
GRANT UPDATE(generation,touched_at) ON resource_query_cursor_owners TO app_login;
CREATE POLICY expired_delete ON resource_query_continuations FOR DELETE TO app_login
 USING(tenant_id=public.current_tenant_id() AND user_id=public.current_actor_id() AND expires_at<=clock_timestamp());
GRANT DELETE ON resource_query_continuations TO app_login;
