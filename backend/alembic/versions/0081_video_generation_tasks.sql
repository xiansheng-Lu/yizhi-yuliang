-- 视频生成编排账本与按秒计价单位；不接真实供应商、不落视频产物。
-- 价目来源由人工验证维护；本迁移只放开VIDEO的SECOND语义，不写入任何价格。
ALTER TABLE generation_price_sources
 DROP CONSTRAINT IF EXISTS generation_price_sources_unit_semantic_check;
ALTER TABLE generation_price_sources
 ADD CONSTRAINT generation_price_sources_unit_semantic_kind_check
 CHECK(unit_semantic='ONE_REQUEST_JOB'
  OR (unit_semantic='ONE_REQUEST_SECOND' AND kind='VIDEO'));

-- 在原generation_fee_start基础上只放宽VIDEO与按秒价目匹配；其余发送守卫不变。
CREATE OR REPLACE FUNCTION access.generation_fee_start(p_job uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE job public.generation_jobs; permit public.ai_cost_permits;
BEGIN
 permit:=access.generation_bound_permit(p_job);
 SELECT * INTO STRICT job FROM public.generation_jobs WHERE id=p_job;
 IF job.submit_state<>'NOT_SENT' OR job.status<>'QUEUED'
  OR job.kind NOT IN ('IMAGE','GRAPHIC','VIDEO')
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
  AND source.price_id=q.price_id
  AND ((source.unit_semantic='ONE_REQUEST_JOB' AND price.unit='JOB')
   OR (source.unit_semantic='ONE_REQUEST_SECOND' AND price.unit='SECOND'
    AND job.kind='VIDEO'))
  AND source.checked_at<=clock_timestamp() AND source.expires_at>clock_timestamp()
  AND price.kind='GENERATION' AND price.unit_scale=1
  AND price.verification='VERIFIED' AND price.valid_from<=clock_timestamp()
  AND price.valid_until>clock_timestamp() FOR SHARE OF source,price;
 IF NOT FOUND THEN RETURN false; END IF;
 RETURN access.ai_fee_start(job.membership_id,permit.id,permit.token_hash,job.request_fingerprint);
END $$;

CREATE TABLE video_generation_tasks (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,job_id uuid NOT NULL,
 provider varchar(40) NOT NULL,external_task_id varchar(128),
 state varchar(12) NOT NULL
  CHECK(state IN ('PENDING','RUNNING','SUCCEEDED','FAILED','TIMEOUT','CANCELLED')),
 attempt_no integer NOT NULL CHECK(attempt_no=1),
 pricing_mode varchar(16) NOT NULL CHECK(pricing_mode IN ('PER_SECOND','PER_CALL')),
 requested_seconds integer CHECK(requested_seconds>0 AND requested_seconds<=600),
 billed_seconds integer CHECK(billed_seconds>0 AND billed_seconds<=600),
 poll_count integer NOT NULL DEFAULT 0 CHECK(poll_count>=0),
 deadline_at timestamptz NOT NULL,
 result_mime varchar(80),
 result_size_bytes bigint CHECK(result_size_bytes>0 AND result_size_bytes<=104857600),
 result_sha256 varchar(64) CHECK(result_sha256 ~ '^[0-9a-f]{64}$'),
 result_duration_seconds integer CHECK(result_duration_seconds>0 AND result_duration_seconds<=600),
 error_code varchar(80),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,job_id),UNIQUE(provider,external_task_id),
 CHECK(pricing_mode='PER_CALL' OR requested_seconds IS NOT NULL),
 FOREIGN KEY(tenant_id,job_id) REFERENCES generation_jobs(tenant_id,id)
);
ALTER TABLE video_generation_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_generation_tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON video_generation_tasks TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON video_generation_tasks FOR SELECT TO app_login USING(
 tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM generation_jobs job
 WHERE (job.tenant_id,job.id)=(video_generation_tasks.tenant_id,video_generation_tasks.job_id)
 AND job.actor_id=public.current_actor_id()));
CREATE POLICY tenant_insert ON video_generation_tasks FOR INSERT TO app_login WITH CHECK(
 tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM generation_jobs job
 WHERE (job.tenant_id,job.id)=(video_generation_tasks.tenant_id,video_generation_tasks.job_id)
 AND job.actor_id=public.current_actor_id() AND job.status='RUNNING' AND job.submit_state='SENT'));
CREATE POLICY tenant_update ON video_generation_tasks FOR UPDATE TO app_login USING(
 tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM generation_jobs job
 WHERE (job.tenant_id,job.id)=(video_generation_tasks.tenant_id,video_generation_tasks.job_id)
 AND job.actor_id=public.current_actor_id()))
 WITH CHECK(tenant_id=public.current_tenant_id());
GRANT SELECT,INSERT ON video_generation_tasks TO app_login;
GRANT UPDATE(state,external_task_id,poll_count,billed_seconds,result_mime,
 result_size_bytes,result_sha256,result_duration_seconds,error_code,version,updated_at)
 ON video_generation_tasks TO app_login;

CREATE FUNCTION public.guard_video_generation_task() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'PENDING' OR NEW.attempt_no<>1 OR NEW.poll_count<>0
   OR NEW.external_task_id IS NOT NULL OR NEW.billed_seconds IS NOT NULL
   OR NEW.result_sha256 IS NOT NULL OR NEW.version<>1 THEN
   RAISE EXCEPTION 'new video task must be pending' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.version<>OLD.version+1 THEN
   RAISE EXCEPTION 'video task revision must advance once' USING ERRCODE='23514'; END IF;
  IF (NEW.tenant_id,NEW.job_id,NEW.pricing_mode,NEW.requested_seconds)
   IS DISTINCT FROM (OLD.tenant_id,OLD.job_id,OLD.pricing_mode,OLD.requested_seconds) THEN
   RAISE EXCEPTION 'video task terms are immutable' USING ERRCODE='23514'; END IF;
  IF OLD.external_task_id IS NOT NULL
   AND NEW.external_task_id IS DISTINCT FROM OLD.external_task_id THEN
   RAISE EXCEPTION 'video external task identity is immutable' USING ERRCODE='23514'; END IF;
  IF OLD.state IN ('SUCCEEDED','FAILED','TIMEOUT','CANCELLED') THEN
   RAISE EXCEPTION 'terminal video task is immutable' USING ERRCODE='23514'; END IF;
  IF NEW.state<>OLD.state AND NOT (
   OLD.state='PENDING' AND NEW.state IN ('RUNNING','FAILED','TIMEOUT','CANCELLED')
   OR OLD.state='RUNNING' AND NEW.state IN ('SUCCEEDED','FAILED','TIMEOUT','CANCELLED')) THEN
   RAISE EXCEPTION 'illegal video task transition' USING ERRCODE='23514'; END IF;
  IF OLD.state='PENDING' AND NEW.state IN ('SUCCEEDED','RUNNING')
   AND NEW.external_task_id IS NULL THEN
   RAISE EXCEPTION 'running video task requires external identity' USING ERRCODE='23514'; END IF;
  IF NEW.state='SUCCEEDED' AND (NEW.result_mime IS NULL OR NEW.result_size_bytes IS NULL
   OR NEW.result_sha256 IS NULL OR NEW.result_duration_seconds IS NULL) THEN
   RAISE EXCEPTION 'video success requires result metadata' USING ERRCODE='23514'; END IF;
  IF NEW.state='SUCCEEDED' AND NEW.pricing_mode='PER_SECOND' AND NEW.billed_seconds IS NULL THEN
   RAISE EXCEPTION 'per-second video success requires billed seconds'
    USING ERRCODE='23514'; END IF;
  IF NEW.state<>'SUCCEEDED' AND (NEW.billed_seconds IS NOT NULL
   OR NEW.result_sha256 IS NOT NULL) THEN
   RAISE EXCEPTION 'video result only on success' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_video_generation_task()
 FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER video_generation_task_guard BEFORE INSERT OR UPDATE
 ON video_generation_tasks FOR EACH ROW EXECUTE FUNCTION public.guard_video_generation_task();
