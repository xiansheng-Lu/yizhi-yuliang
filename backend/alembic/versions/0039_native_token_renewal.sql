-- 不授予app_login对status/expires_at/scopes的直接UPDATE。
CREATE FUNCTION access.renew_provider_token(
 p_member uuid,p_user uuid,p_session uuid,p_id uuid,p_application uuid,p_app text,
 p_connector text,p_version bigint,p_success boolean,p_access bytea,p_refresh bytea,
 p_key integer,p_expires timestamptz,p_scopes text[]
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); grant_row public.provider_authorizations;
BEGIN
 IF p_success IS NULL OR p_user::text IS DISTINCT FROM current_setting('app.current_actor',true)
  OR NOT access.oauth_actor(tenant,p_member,p_user,p_session)
  OR NOT pg_try_advisory_xact_lock(hashtextextended(p_id::text,760119)) THEN
  RETURN false;
 END IF;
 SELECT * INTO grant_row FROM public.provider_authorizations
  WHERE tenant_id=tenant AND id=p_id FOR UPDATE;
 IF NOT FOUND OR grant_row.status='REVOKED' OR grant_row.version<>p_version
  OR (grant_row.application_id,grant_row.app_id,grant_row.connector_id)
   IS DISTINCT FROM (p_application,p_app,p_connector) THEN
  RETURN false;
 END IF;
 IF p_success THEN
  IF p_access IS NULL OR p_key IS NULL OR p_key<1 OR p_scopes IS NULL
   OR p_expires IS NULL OR p_expires<=clock_timestamp()
   OR p_expires>clock_timestamp()+interval '366 days' THEN
   RETURN false;
  END IF;
  UPDATE public.provider_authorizations SET access_token_enc=p_access,refresh_token_enc=p_refresh,
   key_version=p_key,expires_at=p_expires,scopes=p_scopes,status='ACTIVE',version=version+1
   WHERE tenant_id=tenant AND id=p_id;
 ELSE
  IF p_access IS NOT NULL OR p_refresh IS NOT NULL OR p_key IS NOT NULL
   OR p_expires IS NOT NULL OR p_scopes IS NOT NULL THEN RETURN false; END IF;
  UPDATE public.provider_authorizations SET status='REFRESH_FAILED',version=version+1
   WHERE tenant_id=tenant AND id=p_id;
 END IF;
 RETURN true;
END;
$$;
REVOKE ALL ON FUNCTION access.renew_provider_token(uuid,uuid,uuid,uuid,uuid,text,text,bigint,
 boolean,bytea,bytea,integer,timestamptz,text[]) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.renew_provider_token(uuid,uuid,uuid,uuid,uuid,text,text,bigint,
 boolean,bytea,bytea,integer,timestamptz,text[]) TO app_login;
