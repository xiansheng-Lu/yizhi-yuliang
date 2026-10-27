ALTER TABLE ai_invocations ADD COLUMN provider_model varchar(120),
 ADD COLUMN response_hash varchar(64),
 ADD CONSTRAINT ai_response_metadata_check CHECK(
  (provider_model IS NULL AND response_hash IS NULL) OR
  (provider_model IS NOT NULL AND response_hash IS NOT NULL
   AND provider_model ~ '^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}$'
   AND response_hash ~ '^[0-9a-f]{64}$'));

-- 原13参数入口保留兼容；新增入口在同一事务内CAS终结和记录响应溯源。
CREATE FUNCTION access.ai_finish(p_call uuid,p_user uuid,p_status text,p_usage text,p_cost text,
 p_input bigint,p_output bigint,p_amount bigint,p_latency integer,p_external text,p_dispatch text,
 p_currency text,p_provider_model text,p_response_hash text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF (p_provider_model IS NULL)<>(p_response_hash IS NULL) OR
  (p_provider_model IS NOT NULL AND (p_provider_model !~ '^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}$'
   OR p_response_hash !~ '^[0-9a-f]{64}$')) THEN
  RAISE EXCEPTION 'invalid response metadata' USING ERRCODE='22023'; END IF;
 IF NOT access.ai_finish(p_call,p_user,p_status,p_usage,p_cost,p_input,p_output,p_amount,p_latency,
  p_external,p_dispatch,p_currency) THEN RETURN false; END IF;
 UPDATE public.ai_invocations SET provider_model=p_provider_model,response_hash=p_response_hash
 WHERE call_id=p_call AND user_id=p_user AND tenant_id=public.current_tenant_id();
 RETURN true;
END; $$;
REVOKE ALL ON FUNCTION access.ai_finish(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text,text,text)
 FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.ai_finish(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text,text,text)
 TO app_login;
