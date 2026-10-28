ALTER FUNCTION access.ai_begin(uuid,uuid,uuid,uuid,text,text,text,integer,text,uuid)
 RENAME TO ai_begin_actor_internal;
ALTER FUNCTION access.ai_finish(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text)
 RENAME TO ai_finish_actor_internal;
ALTER FUNCTION access.ai_finish(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text,text,text)
 RENAME TO ai_finish_metadata_actor_internal;
REVOKE ALL ON FUNCTION access.ai_begin_actor_internal(uuid,uuid,uuid,uuid,text,text,text,integer,text,uuid),
 access.ai_finish_actor_internal(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text),
 access.ai_finish_metadata_actor_internal(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text,text,text)
 FROM PUBLIC,app_login,app_auth;

CREATE FUNCTION access.ai_begin(p_call uuid,p_request uuid,p_member uuid,p_user uuid,p_scenario text,
 p_alias text,p_prompt text,p_version integer,p_role text,p_parent uuid)
RETURNS TABLE(invocation_id bigint,model_snapshot jsonb,prompt_hash varchar)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF p_user IS NULL OR p_user IS DISTINCT FROM public.current_actor_id() THEN
  RAISE EXCEPTION 'AI actor denied' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT * FROM access.ai_begin_actor_internal(p_call,p_request,p_member,p_user,
  p_scenario,p_alias,p_prompt,p_version,p_role,p_parent);
END $$;

CREATE FUNCTION access.ai_finish(p_call uuid,p_user uuid,p_status text,p_usage text,p_cost text,
 p_input bigint,p_output bigint,p_amount bigint,p_latency integer,p_external text,p_dispatch text,
 p_currency text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF p_user IS NULL OR p_user IS DISTINCT FROM public.current_actor_id() THEN
  RAISE EXCEPTION 'AI actor denied' USING ERRCODE='42501'; END IF;
 RETURN access.ai_finish_actor_internal(p_call,p_user,p_status,p_usage,p_cost,p_input,p_output,
  p_amount,p_latency,p_external,p_dispatch,p_currency);
END $$;

CREATE FUNCTION access.ai_finish(p_call uuid,p_user uuid,p_status text,p_usage text,p_cost text,
 p_input bigint,p_output bigint,p_amount bigint,p_latency integer,p_external text,p_dispatch text,
 p_currency text,p_provider_model text,p_response_hash text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF p_user IS NULL OR p_user IS DISTINCT FROM public.current_actor_id() THEN
  RAISE EXCEPTION 'AI actor denied' USING ERRCODE='42501'; END IF;
 RETURN access.ai_finish_metadata_actor_internal(p_call,p_user,p_status,p_usage,p_cost,p_input,
  p_output,p_amount,p_latency,p_external,p_dispatch,p_currency,p_provider_model,p_response_hash);
END $$;
REVOKE ALL ON FUNCTION access.ai_begin(uuid,uuid,uuid,uuid,text,text,text,integer,text,uuid),
 access.ai_finish(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text),
 access.ai_finish(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text,text,text)
 FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.ai_begin(uuid,uuid,uuid,uuid,text,text,text,integer,text,uuid),
 access.ai_finish(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text),
 access.ai_finish(uuid,uuid,text,text,text,bigint,bigint,bigint,integer,text,text,text,text,text)
 TO app_login;
