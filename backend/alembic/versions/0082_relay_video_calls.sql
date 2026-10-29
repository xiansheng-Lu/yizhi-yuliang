-- 第三方中转站视频调用记录：Web只登记并投递，阻塞生成与产物落盘在后台Worker完成。
-- 每行冻结分辨率、画幅与单价；成功必须带真实字节元数据与实测时长，失败必须带错误码。
CREATE TABLE relay_video_calls (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,membership_id uuid NOT NULL,
 actor_id uuid NOT NULL,shop_id uuid NOT NULL,request_key uuid NOT NULL,
 state varchar(12) NOT NULL CHECK(state IN ('PENDING','RUNNING','SUCCEEDED','FAILED')),
 prompt text NOT NULL CHECK(length(btrim(prompt))>0),
 duration_seconds integer NOT NULL CHECK(duration_seconds>=1 AND duration_seconds<=15),
 resolution varchar(8) NOT NULL CHECK(resolution IN ('480p','768p','1080p')),
 aspect_ratio varchar(16) NOT NULL CHECK(aspect_ratio IN ('portrait','landscape')),
 pricing_mode varchar(16) NOT NULL CHECK(pricing_mode IN ('PER_SECOND','PER_CALL')),
 unit_price_micro_minor bigint NOT NULL
  CHECK(unit_price_micro_minor>0 AND unit_price_micro_minor<=9223372036854775807),
 currency varchar(3) NOT NULL CHECK(currency IN ('CNY','USD')),
 provider_task_id varchar(128),blob_key text,mime varchar(80),
 size_bytes bigint CHECK(size_bytes>0 AND size_bytes<=104857600),
 sha256 varchar(64) CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 measured_duration_seconds integer CHECK(measured_duration_seconds>0 AND measured_duration_seconds<=600),
 settled_amount_micro_minor bigint
  CHECK(settled_amount_micro_minor>0 AND settled_amount_micro_minor<=9223372036854775807),
 error_code varchar(80),error_message text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,request_key),
 CHECK(resolution<>'1080p' OR duration_seconds<=10),
 CHECK(state<>'SUCCEEDED' OR (provider_task_id IS NOT NULL AND blob_key IS NOT NULL
  AND mime='video/mp4' AND size_bytes IS NOT NULL AND sha256 IS NOT NULL
  AND measured_duration_seconds IS NOT NULL AND settled_amount_micro_minor IS NOT NULL)),
 CHECK(state<>'FAILED' OR error_code IS NOT NULL),
 FOREIGN KEY(tenant_id,request_key) REFERENCES generation_jobs(tenant_id,request_key)
);
CREATE INDEX relay_video_calls_running_check ON relay_video_calls(tenant_id,updated_at)
 WHERE state='RUNNING';
ALTER TABLE relay_video_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE relay_video_calls FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_all ON relay_video_calls TO app_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON relay_video_calls FOR SELECT TO app_login USING(
 tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM generation_jobs job
 WHERE (job.tenant_id,job.request_key)=(relay_video_calls.tenant_id,relay_video_calls.request_key)
 AND job.actor_id=public.current_actor_id()));
CREATE POLICY tenant_insert ON relay_video_calls FOR INSERT TO app_login WITH CHECK(
 tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM generation_jobs job
 WHERE (job.tenant_id,job.request_key)=(relay_video_calls.tenant_id,relay_video_calls.request_key)
 AND job.actor_id=public.current_actor_id() AND job.status='RUNNING' AND job.submit_state='SENT'));
CREATE POLICY tenant_update ON relay_video_calls FOR UPDATE TO app_login USING(
 tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM generation_jobs job
 WHERE (job.tenant_id,job.request_key)=(relay_video_calls.tenant_id,relay_video_calls.request_key)
 AND job.actor_id=public.current_actor_id()))
 WITH CHECK(tenant_id=public.current_tenant_id());
GRANT SELECT,INSERT ON relay_video_calls TO app_login;
GRANT UPDATE(state,provider_task_id,blob_key,mime,size_bytes,sha256,
 measured_duration_seconds,settled_amount_micro_minor,error_code,error_message,updated_at)
 ON relay_video_calls TO app_login;

CREATE FUNCTION public.guard_relay_video_call() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'PENDING' OR NEW.provider_task_id IS NOT NULL OR NEW.blob_key IS NOT NULL
   OR NEW.mime IS NOT NULL OR NEW.size_bytes IS NOT NULL OR NEW.sha256 IS NOT NULL
   OR NEW.measured_duration_seconds IS NOT NULL
   OR NEW.settled_amount_micro_minor IS NOT NULL OR NEW.error_code IS NOT NULL THEN
   RAISE EXCEPTION 'new relay video call must be pending' USING ERRCODE='23514'; END IF;
 ELSE
  IF OLD.state IN ('SUCCEEDED','FAILED') THEN
   RAISE EXCEPTION 'terminal relay video call is immutable' USING ERRCODE='23514'; END IF;
  IF (NEW.tenant_id,NEW.membership_id,NEW.actor_id,NEW.shop_id,NEW.request_key,
   NEW.prompt,NEW.duration_seconds,NEW.resolution,NEW.aspect_ratio,NEW.pricing_mode,
   NEW.unit_price_micro_minor,NEW.currency)
   IS DISTINCT FROM (OLD.tenant_id,OLD.membership_id,OLD.actor_id,OLD.shop_id,OLD.request_key,
   OLD.prompt,OLD.duration_seconds,OLD.resolution,OLD.aspect_ratio,OLD.pricing_mode,
   OLD.unit_price_micro_minor,OLD.currency) THEN
   RAISE EXCEPTION 'relay video call terms are immutable' USING ERRCODE='23514'; END IF;
  IF NEW.state<>OLD.state AND NOT (
   OLD.state='PENDING' AND NEW.state IN ('RUNNING','FAILED')
   OR OLD.state='RUNNING' AND NEW.state IN ('SUCCEEDED','FAILED')) THEN
   RAISE EXCEPTION 'illegal relay video call transition' USING ERRCODE='23514'; END IF;
  IF NEW.state='FAILED' AND NEW.error_code IS NULL THEN
   RAISE EXCEPTION 'relay video failure requires error code' USING ERRCODE='23514'; END IF;
  IF NEW.state='SUCCEEDED' AND (NEW.provider_task_id IS NULL OR NEW.blob_key IS NULL
   OR NEW.mime IS NULL OR NEW.size_bytes IS NULL OR NEW.sha256 IS NULL
   OR NEW.measured_duration_seconds IS NULL OR NEW.settled_amount_micro_minor IS NULL) THEN
   RAISE EXCEPTION 'relay video success requires artifact metadata' USING ERRCODE='23514'; END IF;
  IF NEW.state<>'SUCCEEDED' AND NEW.settled_amount_micro_minor IS NOT NULL THEN
   RAISE EXCEPTION 'relay video amount only on success' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_relay_video_call()
 FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER relay_video_call_guard BEFORE INSERT OR UPDATE
 ON relay_video_calls FOR EACH ROW EXECUTE FUNCTION public.guard_relay_video_call();
