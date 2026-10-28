-- 私有绑定读取仅供下面三个受控费用操作，不向应用返回permit capability。
CREATE FUNCTION access.generation_bound_permit(p_job uuid) RETURNS public.ai_cost_permits
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE job public.generation_jobs; permit public.ai_cost_permits;
BEGIN
 SELECT * INTO job FROM public.generation_jobs
 WHERE id=p_job AND tenant_id=public.current_tenant_id()
 AND actor_id=public.current_actor_id();
 IF NOT FOUND THEN RAISE EXCEPTION 'generation denied' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=job.tenant_id AND id=job.run_id FOR UPDATE;
 SELECT * INTO STRICT job FROM public.generation_jobs WHERE id=p_job
 AND tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id() FOR UPDATE;
 SELECT p.* INTO permit FROM public.ai_cost_permits p
 JOIN public.ai_cost_quotes q ON (q.tenant_id,q.id)=(p.tenant_id,p.quote_id)
 WHERE (p.tenant_id,p.id,p.actor_id,p.quote_id,p.request_key,p.kind)=
 (job.tenant_id,job.permit_id,job.actor_id,job.quote_id,job.request_key,'GENERATION')
 AND q.actor_id=job.actor_id AND q.request_key=job.request_key AND q.kind='GENERATION'
 AND q.scenario='content_generation' AND q.request_fingerprint=job.request_fingerprint
 AND q.currency=job.currency AND q.max_cost_minor=job.estimated_cost_minor;
 IF NOT FOUND THEN RAISE EXCEPTION 'generation fee binding denied' USING ERRCODE='42501'; END IF;
 RETURN permit;
END $$;
REVOKE ALL ON FUNCTION access.generation_bound_permit(uuid) FROM PUBLIC,app_login,app_auth;

CREATE FUNCTION public.guard_generation_fee_binding() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'QUEUED' OR NEW.submit_state<>'NOT_SENT' OR NEW.version<>1 THEN
   RAISE EXCEPTION 'new generation must be unsent' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.ai_cost_permits p
   JOIN public.ai_cost_quotes q ON (q.tenant_id,q.id)=(p.tenant_id,p.quote_id)
   WHERE (p.tenant_id,p.id,p.actor_id,p.quote_id,p.request_key,p.kind)=
    (NEW.tenant_id,NEW.permit_id,NEW.actor_id,NEW.quote_id,NEW.request_key,'GENERATION')
   AND p.state='RESERVED' AND q.actor_id=NEW.actor_id AND q.request_key=NEW.request_key
   AND q.kind='GENERATION' AND q.scenario='content_generation'
   AND q.request_fingerprint=NEW.request_fingerprint AND q.currency=NEW.currency
   AND q.max_cost_minor=NEW.estimated_cost_minor AND q.confirmed_at IS NOT NULL
   AND q.expires_at>clock_timestamp()) THEN
   RAISE EXCEPTION 'generation fee binding denied' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.version<>OLD.version+1 THEN
   RAISE EXCEPTION 'generation revision must advance once' USING ERRCODE='23514'; END IF;
  IF OLD.submit_state<>'NOT_SENT' AND NEW.submit_state='NOT_SENT' THEN
   RAISE EXCEPTION 'generation cannot become unsent again' USING ERRCODE='23514'; END IF;
  IF OLD.status IN ('FAILED','CANCELLED','WAITING_REVIEW','SUCCEEDED') AND NEW.status<>OLD.status THEN
   RAISE EXCEPTION 'terminal generation cannot be revived by billing updates'
    USING ERRCODE='23514'; END IF;
  IF OLD.billing_state='KNOWN' AND
    (NEW.billing_state<>'KNOWN' OR NEW.actual_cost_minor IS DISTINCT FROM OLD.actual_cost_minor
     OR NEW.currency IS DISTINCT FROM OLD.currency) THEN
   RAISE EXCEPTION 'known generation cost is immutable' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.submit_state='NOT_SENT' AND NEW.status NOT IN ('QUEUED','CANCELLED') THEN
  RAISE EXCEPTION 'unsent generation cannot claim provider progress' USING ERRCODE='23514'; END IF;
 IF NEW.status='SUCCEEDED' THEN
  RAISE EXCEPTION 'generation acceptance is not integrated' USING ERRCODE='23514'; END IF;
 IF NEW.status='WAITING_REVIEW' AND NOT EXISTS(
  SELECT 1 FROM public.content_assets a JOIN public.content_uploads u
   ON (u.tenant_id,u.id)=(a.tenant_id,a.id)
  WHERE (a.tenant_id,a.id,a.shop_id,a.product_id)=
   (NEW.tenant_id,NEW.result_asset_id,NEW.shop_id,NEW.product_id)
  AND a.deleted_at IS NULL AND u.state='CONFIRMED'
  AND u.object_key=a.object_key AND u.bucket=a.bucket AND u.sha256=a.sha256
  AND u.mime=a.mime AND u.size_bytes=a.size_bytes
  AND NEW.kind IN ('IMAGE','GRAPHIC') AND a.kind='image') THEN
  RAISE EXCEPTION 'generation result requires confirmed matching private image'
   USING ERRCODE='23514'; END IF;
 IF NEW.submit_state<>'NOT_SENT' AND NOT EXISTS(
  SELECT 1 FROM public.generation_dispatches d JOIN public.ai_cost_permits p
   ON (p.tenant_id,p.id)=(NEW.tenant_id,NEW.permit_id)
  WHERE (d.tenant_id,d.job_id,d.request_key,d.payload_hash)=
   (NEW.tenant_id,NEW.id,NEW.request_key,NEW.payload_hash)
  AND p.state IN ('IN_FLIGHT','UNKNOWN','SETTLED')) THEN
  RAISE EXCEPTION 'sent generation requires committed dispatch and fee reservation'
   USING ERRCODE='23514'; END IF;
 IF NEW.billing_state='KNOWN' AND NOT EXISTS(
  SELECT 1 FROM public.ai_cost_permits p WHERE (p.tenant_id,p.id)=(NEW.tenant_id,NEW.permit_id)
  AND p.state='SETTLED' AND p.settled_cost_minor=NEW.actual_cost_minor
  AND p.reported_currency=NEW.currency) THEN
  RAISE EXCEPTION 'known generation fee requires actual settlement' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_generation_fee_binding() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER generation_fee_binding BEFORE INSERT OR UPDATE ON generation_jobs
 FOR EACH ROW EXECUTE FUNCTION public.guard_generation_fee_binding();

CREATE FUNCTION access.generation_fee_start(p_job uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE job public.generation_jobs; permit public.ai_cost_permits;
BEGIN
 permit:=access.generation_bound_permit(p_job);
 SELECT * INTO STRICT job FROM public.generation_jobs WHERE id=p_job;
 IF job.submit_state<>'NOT_SENT' OR job.status<>'QUEUED' OR job.kind NOT IN ('IMAGE','GRAPHIC')
  OR EXISTS(SELECT 1 FROM public.generation_dispatches
    WHERE tenant_id=job.tenant_id AND job_id=job.id) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.content_documents d
  JOIN public.commerce_products product ON
   (product.tenant_id,product.id,product.shop_id)=(d.tenant_id,d.product_id,d.shop_id)
  JOIN public.shops shop ON (shop.tenant_id,shop.id)=(d.tenant_id,d.shop_id)
  JOIN public.ad_accounts account ON
   (account.tenant_id,account.id)=(product.tenant_id,product.account_id)
  JOIN public.entity_links link ON
   (link.tenant_id,link.shop_id,link.account_id)=(d.tenant_id,d.shop_id,account.id)
  JOIN public.workflow_runs run ON (run.tenant_id,run.id)=(d.tenant_id,d.workflow_run_id)
  JOIN public.workflow_run_steps step ON (step.tenant_id,step.run_id)=(run.tenant_id,run.id)
  WHERE (d.tenant_id,d.id,d.shop_id,d.product_id,d.revision)=
   (job.tenant_id,job.document_id,job.shop_id,job.product_id,job.document_revision)
  AND run.id=job.run_id AND run.status='RUNNING' AND step.id=job.step_id
  AND step.attempt=job.step_attempt AND step.status='RUNNING'
  AND d.deleted_at IS NULL AND product.deleted_at IS NULL AND shop.deleted_at IS NULL
  AND account.deleted_at IS NULL AND account.status IN ('BOUND','AUTHORIZED','DEGRADED')
  AND access.configuration_scope(account.id,shop.id,shop.entity_id,'content.write')) THEN
  RETURN false; END IF;
 PERFORM 1 FROM public.generation_price_sources source
  JOIN public.ai_cost_quotes q ON (q.tenant_id,q.id)=(source.tenant_id,job.quote_id)
  JOIN public.ai_price_versions price ON (price.tenant_id,price.id)=(q.tenant_id,q.price_id)
  WHERE source.tenant_id=job.tenant_id AND source.provider=job.provider AND source.kind=job.kind
  AND source.price_id=q.price_id AND source.unit_semantic='ONE_REQUEST_JOB'
  AND source.checked_at<=clock_timestamp() AND source.expires_at>clock_timestamp()
  AND price.kind='GENERATION' AND price.unit='JOB' AND price.unit_scale=1
  AND price.verification='VERIFIED' AND price.valid_from<=clock_timestamp()
  AND price.valid_until>clock_timestamp() FOR SHARE OF source,price;
 IF NOT FOUND THEN RETURN false; END IF;
 RETURN access.ai_fee_start(job.membership_id,permit.id,permit.token_hash,job.request_fingerprint);
END $$;

CREATE FUNCTION access.generation_fee_finish(
 p_job uuid,p_state text,p_amount bigint,p_currency text,p_proof text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE job public.generation_jobs; permit public.ai_cost_permits;
BEGIN
 permit:=access.generation_bound_permit(p_job);
 SELECT * INTO STRICT job FROM public.generation_jobs WHERE id=p_job;
 IF job.submit_state='NOT_SENT' OR NOT EXISTS(
  SELECT 1 FROM public.generation_dispatches WHERE
   (tenant_id,job_id,request_key,payload_hash)=
   (job.tenant_id,job.id,job.request_key,job.payload_hash)) THEN
  RAISE EXCEPTION 'unsent generation cannot settle' USING ERRCODE='22023'; END IF;
 -- 已发费用事实不因报价过期、输入被编辑或新动作权限撤销而被抹除。
 -- 原函数仍核对真实tenant/actor和不可变回执；此函数不采纳内容或授权新发送。
 RETURN access.ai_fee_finish(permit.id,permit.token_hash,p_state,p_amount,p_currency,p_proof);
END $$;

CREATE FUNCTION access.generation_fee_cancel_unsent(p_job uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE job public.generation_jobs; permit public.ai_cost_permits;
BEGIN
 permit:=access.generation_bound_permit(p_job);
 SELECT * INTO STRICT job FROM public.generation_jobs WHERE id=p_job;
 IF job.submit_state<>'NOT_SENT' OR EXISTS(
  SELECT 1 FROM public.generation_dispatches WHERE tenant_id=job.tenant_id AND job_id=job.id)
  THEN RETURN false; END IF;
 RETURN access.ai_fee_cancel(permit.id,permit.token_hash);
END $$;
REVOKE ALL ON FUNCTION access.generation_fee_start(uuid),
 access.generation_fee_finish(uuid,text,bigint,text,text),
 access.generation_fee_cancel_unsent(uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.generation_fee_start(uuid),
 access.generation_fee_finish(uuid,text,bigint,text,text),
 access.generation_fee_cancel_unsent(uuid) TO app_login;
