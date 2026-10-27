ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition','risk.assess',
 'budget.initialize','budget.reserve','idempotency.start','idempotency.complete','idempotency.recover'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK(object_type IN (
 'user','shop','member','role','project','conversation','message','job','platform_request',
 'oauth_state','authorization','sync_source','budget_ledger','idempotency_request'));
CREATE TABLE api_idempotency (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,caller_id uuid NOT NULL,membership_id uuid NOT NULL,
 operation varchar(30) NOT NULL CHECK(operation IN ('JOB_CREATE','JOB_CONFIRM','JOB_APPROVE','JOB_REJECT','JOB_CANCEL','JOB_SAFE_RETRY')),
 key_digest varchar(64) NOT NULL CHECK(key_digest ~ '^[0-9a-f]{64}$'),
 payload_digest varchar(64) NOT NULL CHECK(payload_digest ~ '^[0-9a-f]{64}$'),
 authorization_ref jsonb NOT NULL CHECK(jsonb_typeof(authorization_ref)='object'),
 state varchar(12) NOT NULL CHECK(state IN ('IN_FLIGHT','COMPLETED')),
 owner_digest varchar(64) NOT NULL CHECK(owner_digest ~ '^[0-9a-f]{64}$'),lease_until timestamptz NOT NULL,
 request_id uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,status_code integer,response_body jsonb,recovery_count bigint NOT NULL DEFAULT 0 CHECK(recovery_count>=0),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,caller_id,operation,key_digest),
 FOREIGN KEY(tenant_id,membership_id,caller_id) REFERENCES memberships(tenant_id,id,user_id),
 CHECK(state<>'COMPLETED' OR (completed_at IS NOT NULL AND jsonb_typeof(response_body)='object'
  AND (status_code BETWEEN 200 AND 299 OR status_code BETWEEN 400 AND 499)) IS TRUE)
);
-- 不按短TTL删除键。仅支持同PG事务操作，外部写由execution attempt账承担。
ALTER TABLE api_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_idempotency FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON api_idempotency TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY private_caller ON api_idempotency TO app_login
 USING(tenant_id=public.current_tenant_id() AND caller_id=public.current_actor_id())
 WITH CHECK(tenant_id=public.current_tenant_id() AND caller_id=public.current_actor_id());
GRANT SELECT,INSERT ON api_idempotency TO app_login;

CREATE FUNCTION access.idempotency_entry(p_member uuid,p_user uuid,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); item public.api_idempotency;
BEGIN
 IF p_user IS DISTINCT FROM public.current_actor_id() OR NOT EXISTS(
  SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.tenants t ON t.id=m.tenant_id WHERE m.tenant_id=tenant AND m.id=p_member AND m.user_id=p_user
   AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL AND t.status='ACTIVE' AND t.deleted_at IS NULL
 ) THEN RAISE EXCEPTION 'idempotency caller unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO item FROM public.api_idempotency WHERE tenant_id=tenant AND id=p_id AND caller_id=p_user
  FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'idempotency request unavailable' USING ERRCODE='P0002'; END IF;
 RETURN to_jsonb(item);
END $$;
REVOKE ALL ON FUNCTION access.idempotency_entry(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.idempotency_entry(uuid,uuid,uuid) TO app_login;

CREATE FUNCTION access.idempotency_lock(p_member uuid,p_user uuid,p_id uuid,p_secret text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item jsonb;
BEGIN
 item:=access.idempotency_entry(p_member,p_user,p_id);
 IF item->>'state'='COMPLETED' THEN RETURN item; END IF;
 IF p_secret IS NULL OR length(p_secret) NOT BETWEEN 32 AND 256 OR
  item->>'owner_digest' IS DISTINCT FROM encode(sha256(convert_to(p_secret,'UTF8')),'hex') THEN
  RAISE EXCEPTION 'idempotency owner changed' USING ERRCODE='40001'; END IF;
 IF (item->>'lease_until')::timestamptz<=clock_timestamp() THEN
  RAISE EXCEPTION 'idempotency recovery required' USING ERRCODE='55P03'; END IF;
 RETURN item;
END $$;
REVOKE ALL ON FUNCTION access.idempotency_lock(uuid,uuid,uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.idempotency_lock(uuid,uuid,uuid,text) TO app_login;

CREATE FUNCTION access.idempotency_finish(p_member uuid,p_user uuid,p_id uuid,p_secret text,p_status integer,p_body jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item jsonb;
BEGIN
 item:=access.idempotency_entry(p_member,p_user,p_id);
 IF item->>'state'<>'IN_FLIGHT' OR p_secret IS NULL
  OR item->>'owner_digest' IS DISTINCT FROM encode(sha256(convert_to(p_secret,'UTF8')),'hex')
  OR p_status IS NULL OR NOT(p_status BETWEEN 200 AND 299 OR p_status BETWEEN 400 AND 499)
  OR jsonb_typeof(p_body) IS DISTINCT FROM 'object' OR octet_length(p_body::text)>131072 THEN
  RAISE EXCEPTION 'idempotency completion conflict' USING ERRCODE='40001'; END IF;
 UPDATE public.api_idempotency SET state='COMPLETED',completed_at=clock_timestamp(),
  status_code=p_status,response_body=p_body WHERE id=p_id AND tenant_id=public.current_tenant_id();
END $$;
REVOKE ALL ON FUNCTION access.idempotency_finish(uuid,uuid,uuid,text,integer,jsonb) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.idempotency_finish(uuid,uuid,uuid,text,integer,jsonb) TO app_login;

CREATE FUNCTION access.idempotency_recover(p_member uuid,p_user uuid,p_id uuid,p_secret text,p_seconds integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item jsonb;
BEGIN
 item:=access.idempotency_entry(p_member,p_user,p_id);
 IF item->>'state'='COMPLETED' THEN RETURN item; END IF;
 IF (item->>'lease_until')::timestamptz>clock_timestamp() THEN
  RAISE EXCEPTION 'original request in flight' USING ERRCODE='55P03'; END IF;
 IF p_secret IS NULL OR length(p_secret) NOT BETWEEN 32 AND 256 OR p_seconds IS NULL
  OR p_seconds NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'invalid recovery' USING ERRCODE='22023'; END IF;
 UPDATE public.api_idempotency SET owner_digest=encode(sha256(convert_to(p_secret,'UTF8')),'hex'),
  lease_until=clock_timestamp()+make_interval(secs=>p_seconds),recovery_count=recovery_count+1
  WHERE id=p_id AND tenant_id=public.current_tenant_id() RETURNING to_jsonb(api_idempotency.*) INTO item;
 RETURN item;
END $$;
REVOKE ALL ON FUNCTION access.idempotency_recover(uuid,uuid,uuid,text,integer) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.idempotency_recover(uuid,uuid,uuid,text,integer) TO app_login;
