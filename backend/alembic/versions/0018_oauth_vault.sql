ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK(object_type IN (
 'user','shop','member','role','project','conversation','message','job','platform_request',
 'oauth_state','authorization'));

CREATE TABLE oauth_states (
 id uuid PRIMARY KEY, tenant_id bigint NOT NULL REFERENCES tenants(id),
 user_id uuid NOT NULL REFERENCES users(id), membership_id uuid NOT NULL, session_id uuid NOT NULL,
 authorization_id uuid NOT NULL UNIQUE, request_id uuid NOT NULL,
 application_id uuid NOT NULL, app_id varchar(128) NOT NULL,
 connector_id varchar(20) NOT NULL, provider varchar(20) NOT NULL, product_line varchar(20) NOT NULL,
 redirect_digest varchar(64) NOT NULL CHECK(redirect_digest ~ '^[0-9a-f]{64}$'),
 state_digest varchar(64) NOT NULL UNIQUE CHECK(state_digest ~ '^[0-9a-f]{64}$'),
 state varchar(12) NOT NULL DEFAULT 'PENDING'
  CHECK(state IN ('PENDING','CLAIMED','SUCCEEDED','FAILED','UNKNOWN')),
 expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 claimed_at timestamptz, completed_at timestamptz,
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,membership_id,user_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,session_id) REFERENCES sessions(tenant_id,id),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '10 minutes'),
 CHECK((connector_id,provider,product_line) IN (
  ('adq','tencent','adq'),('wxpromo','tencent','shop'),('wxshop','wxshop','wxshop')))
);
CREATE INDEX ix_oauth_state_pending ON oauth_states(expires_at) WHERE state='PENDING';

CREATE TABLE provider_authorizations (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL REFERENCES tenants(id),
 account_id uuid, state_id uuid NOT NULL UNIQUE, authorized_by uuid NOT NULL REFERENCES users(id),
 application_id uuid NOT NULL,app_id varchar(128) NOT NULL,
 connector_id varchar(20) NOT NULL,provider varchar(20) NOT NULL,product_line varchar(20) NOT NULL,
 access_token_enc bytea NOT NULL,refresh_token_enc bytea,key_version integer NOT NULL CHECK(key_version>0),
 scopes text[] NOT NULL,expires_at timestamptz NOT NULL,
 status varchar(20) NOT NULL CHECK(status IN ('ACTIVE','EXPIRED','REVOKED','REFRESH_FAILED')),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 authorized_at timestamptz NOT NULL DEFAULT clock_timestamp(),last_verified_at timestamptz,
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,state_id) REFERENCES oauth_states(tenant_id,id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 CHECK(octet_length(access_token_enc) BETWEEN 29 AND 16412),
 CHECK(refresh_token_enc IS NULL OR octet_length(refresh_token_enc) BETWEEN 29 AND 16412),
 CHECK((connector_id,provider,product_line) IN (
  ('adq','tencent','adq'),('wxpromo','tencent','shop'),('wxshop','wxshop','wxshop')))
);
CREATE TABLE authorization_exchange_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 tenant_id bigint NOT NULL REFERENCES tenants(id),state_id uuid NOT NULL,
 request_id uuid NOT NULL,phase varchar(12) NOT NULL
  CHECK(phase IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN')),
 code_digest varchar(64) NOT NULL CHECK(code_digest ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(state_id,phase), FOREIGN KEY(tenant_id,state_id) REFERENCES oauth_states(tenant_id,id)
);
CREATE UNIQUE INDEX ux_oauth_terminal ON authorization_exchange_events(state_id) WHERE phase<>'STARTED';

DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['oauth_states','provider_authorizations','authorization_exchange_events'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I FOR ALL TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY tenant_read ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('CREATE POLICY tenant_insert ON %I FOR INSERT TO app_login WITH CHECK(tenant_id=public.current_tenant_id())',item);
  EXECUTE format('GRANT SELECT,INSERT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE POLICY tenant_update ON oauth_states FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
CREATE POLICY tenant_update ON provider_authorizations FOR UPDATE TO app_login
 USING(tenant_id=public.current_tenant_id()) WITH CHECK(tenant_id=public.current_tenant_id());
GRANT UPDATE(state,completed_at) ON oauth_states TO app_login;
GRANT UPDATE(access_token_enc,refresh_token_enc,key_version,version) ON provider_authorizations TO app_login;
GRANT USAGE ON SEQUENCE authorization_exchange_events_id_seq TO app_login;

-- 只返回管理员是否仍有效，避免回调借另一设备Cookie切换身份。
CREATE FUNCTION access.oauth_actor(p_tenant bigint,p_member uuid,p_user uuid,p_session uuid)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(
  SELECT 1 FROM public.sessions s
  JOIN public.memberships m ON (m.tenant_id,m.id,m.user_id)=(s.tenant_id,s.membership_id,s.user_id)
  JOIN public.users u ON u.id=s.user_id JOIN public.tenants t ON t.id=s.tenant_id
  WHERE s.id=p_session AND s.tenant_id=p_tenant AND s.membership_id=p_member AND s.user_id=p_user
   AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
   AND s.absolute_expires_at>clock_timestamp() AND s.authz_version=m.authz_version
   AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND t.status='ACTIVE' AND t.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions p
    ON (p.tenant_id,p.role_id)=(g.tenant_id,g.role_id)
    WHERE g.tenant_id=m.tenant_id AND g.membership_id=m.id AND p.permission_code='connection.manage')
   AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.tenant_id=m.tenant_id AND ds.membership_id=m.id
    AND ds.scope_type='ALL_TENANT' AND (ds.permissions_mask IS NULL OR 'connection.manage'=ANY(ds.permissions_mask)))
 );
$$;
REVOKE ALL ON FUNCTION access.oauth_actor(bigint,uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.oauth_actor(bigint,uuid,uuid,uuid) TO app_login;

-- 精确高熵state的HMAC+服务端应用上下文是回调唯一入口；无全表/租户枚举。
CREATE FUNCTION access.oauth_claim(p_digest text,p_application uuid,p_app text,p_connector text,
 p_redirect text) RETURNS SETOF public.oauth_states
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 UPDATE public.oauth_states s SET state='CLAIMED',claimed_at=clock_timestamp()
 WHERE s.state_digest=p_digest AND s.application_id=p_application AND s.app_id=p_app
  AND s.connector_id=p_connector AND s.redirect_digest=p_redirect
  AND s.state='PENDING' AND s.expires_at>clock_timestamp()
  AND access.oauth_actor(s.tenant_id,s.membership_id,s.user_id,s.session_id)
 RETURNING s.*;
$$;
REVOKE ALL ON FUNCTION access.oauth_claim(text,uuid,text,text,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.oauth_claim(text,uuid,text,text,text) TO app_login;
