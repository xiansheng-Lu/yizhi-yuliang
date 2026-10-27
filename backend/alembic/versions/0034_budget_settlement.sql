CREATE TABLE budget_job_finalizations (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,job_id uuid NOT NULL,created_by uuid NOT NULL,
 kind varchar(8) NOT NULL CHECK(kind IN ('MATCH','RELEASE')),proof_kind varchar(30) NOT NULL
 CHECK(proof_kind IN ('VERIFIED_MATCH','UNCLAIMED_TERMINAL','PLATFORM_REJECTION')),
 verified_fact_id uuid,receipt_id uuid,result jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,job_id),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id),
 FOREIGN KEY(verified_fact_id) REFERENCES verified_action_facts(id),
 FOREIGN KEY(tenant_id,receipt_id) REFERENCES execution_receipts(tenant_id,id),
 CHECK((kind='MATCH')=(verified_fact_id IS NOT NULL)),
 CHECK((proof_kind='PLATFORM_REJECTION')=(receipt_id IS NOT NULL))
);
CREATE TABLE budget_adjustments (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,finalization_id uuid NOT NULL,ledger_id uuid NOT NULL,
 campaign_id uuid NOT NULL,account_id uuid NOT NULL,observation_id uuid NOT NULL,
 confirmed_before bigint NOT NULL,confirmed_after bigint NOT NULL,reserved_before bigint NOT NULL,
 reserved_after bigint NOT NULL,component_before bigint,component_after bigint,budget_delta bigint NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,finalization_id,ledger_id),
 FOREIGN KEY(tenant_id,finalization_id) REFERENCES budget_job_finalizations(tenant_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(tenant_id,ledger_id) REFERENCES budget_ledgers(tenant_id,id),
 FOREIGN KEY(tenant_id,observation_id) REFERENCES budget_observations(tenant_id,id),
 FOREIGN KEY(tenant_id,campaign_id,account_id) REFERENCES campaigns(tenant_id,id,account_id),
 CHECK(confirmed_after>=0 AND reserved_after>=0),CHECK(confirmed_after-confirmed_before=budget_delta)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['budget_job_finalizations','budget_adjustments'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;
CREATE POLICY private_job ON budget_job_finalizations FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND created_by=public.current_actor_id());
CREATE POLICY private_job ON budget_adjustments FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM budget_job_finalizations f
  WHERE f.tenant_id=budget_adjustments.tenant_id AND f.id=budget_adjustments.finalization_id));

CREATE FUNCTION access.budget_match_context(p_member uuid,p_user uuid,p_fact uuid,p_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); fact public.verified_action_facts;
 job public.command_jobs; item public.execution_orders; attempt public.execution_attempts;
 claim public.target_guard_claims; guard public.target_execution_guards; receipt public.execution_receipts;
 finished public.budget_job_finalizations;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT * INTO fact FROM public.verified_action_facts WHERE tenant_id=tenant AND id=p_fact;
 IF NOT FOUND THEN RAISE EXCEPTION 'verified evidence unavailable' USING ERRCODE='P0002'; END IF;
 PERFORM access.job_state_context(p_member,p_user,fact.job_id,'CLAIM');
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=fact.job_id FOR UPDATE;
 SELECT * INTO finished FROM public.budget_job_finalizations WHERE tenant_id=tenant AND job_id=job.id;
 IF FOUND THEN
  IF finished.kind<>'MATCH' OR finished.verified_fact_id<>p_fact THEN
   RAISE EXCEPTION 'finalized proof cannot change' USING ERRCODE='23505'; END IF;
  RETURN jsonb_build_object('existing',finished.result);
 END IF;
 IF job.status<>'VERIFYING' OR job.version<>p_version OR job.document->>'action_type'<>'CHANGE_DAILY_BUDGET'
  OR fact.action_type<>'CHANGE_DAILY_BUDGET' OR fact.observed_value IS DISTINCT FROM job.document->'new_value'
  OR job.document#>>'{old_value,currency}' IS DISTINCT FROM 'CNY'
  OR job.document#>>'{new_value,currency}' IS DISTINCT FROM 'CNY'
  OR job.document#>>'{execution_receipt,accepted}' IS DISTINCT FROM 'true' THEN
  RAISE EXCEPTION 'verified job version/value required' USING ERRCODE='40001'; END IF;
 SELECT o.* INTO item FROM public.execution_orders o JOIN public.platform_requests r
  ON (r.tenant_id,r.execution_id)=(o.tenant_id,o.id) WHERE o.tenant_id=tenant
   AND r.id=fact.write_outbound_id AND o.command_job_id=job.id AND o.status='VERIFYING' FOR UPDATE OF o;
 IF NOT FOUND THEN RAISE EXCEPTION 'current execution acceptance required' USING ERRCODE='22023'; END IF;
 SELECT a.* INTO attempt FROM public.execution_attempts a WHERE a.tenant_id=tenant AND a.order_id=item.id
  AND a.attempt_no=item.current_attempt AND a.outbound_id=fact.write_outbound_id AND a.state='ACK_ACCEPTED' FOR UPDATE;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.platform_requests r WHERE r.tenant_id=tenant
  AND r.id=fact.read_outbound_id AND r.execution_id=item.id AND r.execution_attempt_id=attempt.id AND r.effect='READ') THEN
  RAISE EXCEPTION 'current attempt independent read required' USING ERRCODE='22023'; END IF;
 SELECT * INTO receipt FROM public.execution_receipts WHERE tenant_id=tenant AND attempt_id=attempt.id
  AND id=(job.document#>>'{execution_receipt,receipt_id}')::uuid AND applied AND accepted=true AND source='PLATFORM'
  AND raw_id=fact.write_payload_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'applied acceptance proof required' USING ERRCODE='22023'; END IF;
 SELECT * INTO claim FROM public.target_guard_claims WHERE tenant_id=tenant AND id=attempt.guard_claim_id;
 SELECT * INTO guard FROM public.target_execution_guards WHERE tenant_id=tenant AND id=claim.guard_id FOR UPDATE;
 IF guard.fence IS DISTINCT FROM claim.fence OR guard.job_id IS DISTINCT FROM job.id
  OR guard.lease_owner IS DISTINCT FROM claim.lease_owner OR guard.state NOT IN ('LEASED','QUARANTINED') THEN
  RAISE EXCEPTION 'old fence match isolated' USING ERRCODE='40001'; END IF;
 RETURN jsonb_build_object('job',to_jsonb(job),'fact',to_jsonb(fact),'order',to_jsonb(item),
  'attempt',to_jsonb(attempt),'guard',to_jsonb(guard));
END $$;
REVOKE ALL ON FUNCTION access.budget_match_context(uuid,uuid,uuid,bigint) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_match_context(uuid,uuid,uuid,bigint) TO app_login;

CREATE FUNCTION access.budget_settle_match(p_member uuid,p_user uuid,p_fact uuid,p_version bigint,
 p_id uuid,p_document jsonb,p_request uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); ctx jsonb; job public.command_jobs; target public.campaigns;
 ledger public.budget_ledgers; observation public.budget_observations; previous public.budget_adjustments;
 reservation public.budget_reservations; hold public.job_budget_holds; fact public.verified_action_facts;
 component jsonb; actual bigint; amount bigint; delta bigint; change bigint; held_sum numeric;
 new_total bigint; new_reserved bigint; frozen text; proof text; result jsonb; bucket_count integer:=0;
BEGIN
 ctx:=access.budget_match_context(p_member,p_user,p_fact,p_version);
 IF ctx?'existing' THEN RETURN ctx->'existing'; END IF;
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=(ctx#>>'{job,id}')::uuid;
 SELECT * INTO fact FROM public.verified_action_facts WHERE tenant_id=tenant AND id=p_fact;
 SELECT * INTO target FROM public.campaigns WHERE tenant_id=tenant AND id=job.campaign_id;
 IF ((p_document->'execution_receipt')-ARRAY['verification','observed_value','verified_at'])
    IS DISTINCT FROM ((job.document->'execution_receipt')-ARRAY['verification','observed_value','verified_at'])
  OR p_document#>>'{execution_receipt,verification}' IS DISTINCT FROM 'MATCHED'
  OR p_document#>'{execution_receipt,observed_value}' IS DISTINCT FROM fact.observed_value
  OR (p_document#>>'{execution_receipt,verified_at}')::timestamptz IS DISTINCT FROM fact.verified_at
  OR p_document->'risk_summary' IS DISTINCT FROM job.document->'risk_summary'
  OR p_document->'approval' IS DISTINCT FROM job.document->'approval'
  OR p_document->'error' IS DISTINCT FROM 'null'::jsonb THEN
  RAISE EXCEPTION 'matched document differs from E2' USING ERRCODE='22023'; END IF;
 delta:=(job.document#>>'{new_value,amount_minor}')::bigint-(job.document#>>'{old_value,amount_minor}')::bigint;
 SELECT * INTO hold FROM public.job_budget_holds WHERE tenant_id=tenant AND job_id=job.id FOR UPDATE;
 IF delta>0 AND (NOT FOUND OR hold.state<>'HELD' OR hold.intent_hash<>job.intent_hash
  OR hold.account_id<>job.account_id OR hold.campaign_id<>job.campaign_id) THEN
  RAISE EXCEPTION 'positive match requires held reservations' USING ERRCODE='22023'; END IF;
 FOR ledger IN SELECT l.* FROM public.budget_ledgers l WHERE l.tenant_id=tenant AND
  (EXISTS(SELECT 1 FROM public.budget_reservations r WHERE r.tenant_id=tenant AND r.job_id=job.id AND r.ledger_id=l.id)
   OR (delta<=0 AND l.bucket_key='TENANT_DAILY' AND l.currency='CNY' AND job.account_id=ANY(l.covered_account_ids)))
  ORDER BY l.id FOR UPDATE LOOP
  bucket_count:=bucket_count+1;
  IF ledger.confirmed_total_minor IS NULL OR ledger.currency<>'CNY' OR ledger.data_state<>'CONFIRMED' THEN
   RAISE EXCEPTION 'known budget bucket required' USING ERRCODE='22023'; END IF;
  SELECT * INTO observation FROM public.budget_observations WHERE tenant_id=tenant AND ledger_id=ledger.id
   ORDER BY recorded_at DESC,id DESC LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION 'full coverage component basis required' USING ERRCODE='22023'; END IF;
  SELECT value INTO component FROM jsonb_array_elements(observation.components)
   WHERE value->>'accountId'=job.account_id::text AND value->>'nativeId'=target.native_id;
  IF component IS NULL THEN RAISE EXCEPTION 'target not covered by confirmed B' USING ERRCODE='22023'; END IF;
  actual:=(component->>'budgetMinor')::bigint;
  SELECT * INTO previous FROM public.budget_adjustments a WHERE a.tenant_id=tenant AND a.ledger_id=ledger.id
   AND a.campaign_id=job.campaign_id AND a.observation_id=observation.id AND a.component_after IS NOT NULL
   ORDER BY a.created_at DESC,a.id DESC LIMIT 1;
  IF FOUND THEN actual:=previous.component_after; END IF;
  -- 完整同步可能已经包含新值；只补该组件尚未记入B的差额，不能再次B+=delta。
  IF actual NOT IN ((job.document#>>'{old_value,amount_minor}')::bigint,
                   (job.document#>>'{new_value,amount_minor}')::bigint) THEN
   RAISE EXCEPTION 'external budget component drift requires reconciliation' USING ERRCODE='22023'; END IF;
  change:=(job.document#>>'{new_value,amount_minor}')::bigint-actual;
  IF change<>0 AND EXISTS(SELECT 1 FROM public.platform_raw_payloads r
   JOIN public.platform_raw_payloads observed ON observed.tenant_id=r.tenant_id
   WHERE r.tenant_id=tenant AND r.outbound_id=(component->>'outboundId')::uuid AND r.phase='RESPONSE'
    AND observed.id=fact.read_payload_id AND r.created_at>observed.created_at) THEN
   RAISE EXCEPTION 'newer budget observation requires fresh match' USING ERRCODE='22023'; END IF;
  SELECT coalesce(sum(amount_minor),0) INTO held_sum FROM public.budget_reservations
   WHERE tenant_id=tenant AND ledger_id=ledger.id AND state='HELD';
  IF held_sum<>ledger.reserved_increase_minor THEN RAISE EXCEPTION 'held aggregate differs' USING ERRCODE='22023'; END IF;
  amount:=0;
  IF delta>0 THEN
   SELECT * INTO reservation FROM public.budget_reservations WHERE tenant_id=tenant AND job_id=job.id AND ledger_id=ledger.id FOR UPDATE;
   IF NOT FOUND OR reservation.state<>'HELD' OR reservation.amount_minor<>delta OR reservation.hold_id<>hold.id THEN
    RAISE EXCEPTION 'held delta differs' USING ERRCODE='22023'; END IF;
   amount:=reservation.amount_minor;
  END IF;
  new_total:=ledger.confirmed_total_minor+change; new_reserved:=ledger.reserved_increase_minor-amount;
  frozen:=CASE WHEN ledger.freeze_reason IS NOT NULL AND ledger.freeze_reason<>'OVER_LIMIT' THEN ledger.freeze_reason
   WHEN new_total+new_reserved>ledger.limit_minor THEN 'OVER_LIMIT' END;
  UPDATE public.budget_ledgers SET confirmed_total_minor=new_total,reserved_increase_minor=new_reserved,
   freeze_reason=frozen,version=version+1 WHERE tenant_id=tenant AND id=ledger.id;
  INSERT INTO public.budget_adjustments(id,tenant_id,finalization_id,ledger_id,campaign_id,account_id,observation_id,
   confirmed_before,confirmed_after,reserved_before,reserved_after,component_before,component_after,budget_delta)
  VALUES(gen_random_uuid(),tenant,p_id,ledger.id,job.campaign_id,job.account_id,observation.id,
   ledger.confirmed_total_minor,new_total,ledger.reserved_increase_minor,new_reserved,actual,
   (job.document#>>'{new_value,amount_minor}')::bigint,change);
 END LOOP;
 IF bucket_count=0 THEN RAISE EXCEPTION 'applicable budget bucket missing' USING ERRCODE='22023'; END IF;
 IF delta>0 THEN
  UPDATE public.budget_reservations SET state='SETTLED' WHERE tenant_id=tenant AND job_id=job.id AND state='HELD';
  UPDATE public.job_budget_holds SET state='SETTLED' WHERE tenant_id=tenant AND job_id=job.id AND state='HELD';
 END IF;
 proof:=encode(sha256(convert_to('verified-match|'||p_fact::text,'UTF8')),'hex');
 PERFORM access.job_apply_transition(p_member,p_user,job.id,p_version,'MATCH',p_document,p_request,proof);
 UPDATE public.execution_orders SET status='EFFECTIVE',version=version+1,updated_at=clock_timestamp()
  WHERE tenant_id=tenant AND id=(ctx#>>'{order,id}')::uuid;
 UPDATE public.target_execution_guards SET state='IDLE',job_id=NULL,lease_owner=NULL,lease_until=NULL
  WHERE tenant_id=tenant AND id=(ctx#>>'{guard,id}')::uuid;
 result:=jsonb_build_object('finalization_id',p_id,'job_id',job.id,'kind','MATCH','proof_kind','VERIFIED_MATCH',
  'job_version',p_version+1,'buckets',bucket_count,'verified_fact_id',p_fact);
 INSERT INTO public.budget_job_finalizations(id,tenant_id,job_id,created_by,kind,proof_kind,verified_fact_id,result)
  VALUES(p_id,tenant,job.id,p_user,'MATCH','VERIFIED_MATCH',p_fact,result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION access.budget_settle_match(uuid,uuid,uuid,bigint,uuid,jsonb,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_settle_match(uuid,uuid,uuid,bigint,uuid,jsonb,uuid) TO app_login;

CREATE FUNCTION access.budget_release(p_member uuid,p_user uuid,p_job uuid,p_receipt uuid,p_id uuid,p_request uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); job public.command_jobs; hold public.job_budget_holds;
 ledger public.budget_ledgers; reservation public.budget_reservations; receipt public.execution_receipts;
 item public.execution_orders; attempt public.execution_attempts; claim public.target_guard_claims;
 guard public.target_execution_guards; observation public.budget_observations;
 finished public.budget_job_finalizations; proof_kind text; result jsonb; buckets integer:=0; held_sum numeric;
 frozen text; next_reserved bigint;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job;
 IF NOT FOUND THEN RAISE EXCEPTION 'job unavailable' USING ERRCODE='P0002'; END IF;
 PERFORM access.job_state_context(p_member,p_user,p_job,
  CASE WHEN job.status='REJECTED' AND job.created_by<>p_user THEN 'REJECT' ELSE 'CLAIM' END);
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job FOR UPDATE;
 SELECT * INTO finished FROM public.budget_job_finalizations WHERE tenant_id=tenant AND job_id=p_job;
 IF FOUND THEN
  IF finished.kind<>'RELEASE' OR finished.receipt_id IS DISTINCT FROM p_receipt THEN
   RAISE EXCEPTION 'original finalization proof required' USING ERRCODE='23505'; END IF;
  RETURN jsonb_build_object('result',finished.result,'created',false);
 END IF;
 IF p_receipt IS NULL THEN
  IF job.status NOT IN ('REJECTED','CANCELLED','EXPIRED') OR job.claimed_at IS NOT NULL
   OR job.dispatch_started_at IS NOT NULL OR EXISTS(SELECT 1 FROM public.execution_orders
    WHERE tenant_id=tenant AND command_job_id=p_job) OR NOT EXISTS(
     SELECT 1 FROM public.job_events e WHERE e.tenant_id=tenant AND e.job_id=p_job AND e.job_version=job.version
      AND e.control_event=CASE job.status WHEN 'REJECTED' THEN 'REJECT' WHEN 'CANCELLED' THEN 'CANCEL' ELSE 'EXPIRE' END) THEN
   RAISE EXCEPTION 'durable unclaimed terminal proof required' USING ERRCODE='22023'; END IF;
  proof_kind:='UNCLAIMED_TERMINAL';
 ELSE
  SELECT * INTO receipt FROM public.execution_receipts WHERE tenant_id=tenant AND id=p_receipt
   AND source='PLATFORM' AND accepted=false AND evidence_level='E1' AND applied;
  IF NOT FOUND OR job.status<>'FAILED' OR job.document#>>'{execution_receipt,receipt_id}' IS DISTINCT FROM p_receipt::text THEN
   RAISE EXCEPTION 'applied E1 rejection required' USING ERRCODE='22023'; END IF;
  SELECT * INTO item FROM public.execution_orders WHERE tenant_id=tenant AND id=receipt.order_id AND command_job_id=p_job FOR UPDATE;
  SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=tenant AND id=receipt.attempt_id AND order_id=item.id
   AND attempt_no=item.current_attempt AND state='REJECTED' FOR UPDATE;
  IF NOT FOUND OR item.status<>'FAILED' THEN RAISE EXCEPTION 'current rejected attempt required' USING ERRCODE='22023'; END IF;
  SELECT * INTO claim FROM public.target_guard_claims WHERE tenant_id=tenant AND id=attempt.guard_claim_id;
  SELECT * INTO guard FROM public.target_execution_guards WHERE tenant_id=tenant AND id=claim.guard_id FOR UPDATE;
  IF guard.fence IS DISTINCT FROM claim.fence OR guard.job_id IS DISTINCT FROM p_job
   OR guard.lease_owner IS DISTINCT FROM claim.lease_owner OR guard.state NOT IN ('LEASED','QUARANTINED') THEN
   RAISE EXCEPTION 'old fence release isolated' USING ERRCODE='40001'; END IF;
  proof_kind:='PLATFORM_REJECTION';
 END IF;
 SELECT * INTO hold FROM public.job_budget_holds WHERE tenant_id=tenant AND job_id=p_job FOR UPDATE;
 IF FOUND AND hold.state<>'HELD' THEN RAISE EXCEPTION 'hold already finalized without matching journal' USING ERRCODE='22023'; END IF;
 IF hold.id IS NOT NULL AND hold.intent_hash<>job.intent_hash THEN
  RAISE EXCEPTION 'hold intent differs' USING ERRCODE='22023'; END IF;
 FOR ledger IN SELECT l.* FROM public.budget_ledgers l JOIN public.budget_reservations r
  ON (r.tenant_id,r.ledger_id)=(l.tenant_id,l.id) WHERE r.tenant_id=tenant AND r.job_id=p_job ORDER BY l.id FOR UPDATE OF l LOOP
  SELECT * INTO reservation FROM public.budget_reservations WHERE tenant_id=tenant AND job_id=p_job AND ledger_id=ledger.id FOR UPDATE;
  SELECT coalesce(sum(amount_minor),0) INTO held_sum FROM public.budget_reservations WHERE tenant_id=tenant
   AND ledger_id=ledger.id AND state='HELD';
  IF reservation.state<>'HELD' OR held_sum<>ledger.reserved_increase_minor OR reservation.hold_id IS DISTINCT FROM hold.id THEN
   RAISE EXCEPTION 'reservation aggregate changed' USING ERRCODE='22023'; END IF;
  SELECT * INTO observation FROM public.budget_observations WHERE tenant_id=tenant AND ledger_id=ledger.id
   ORDER BY recorded_at DESC,id DESC LIMIT 1;
  IF NOT FOUND OR ledger.confirmed_total_minor IS NULL THEN RAISE EXCEPTION 'known budget basis required' USING ERRCODE='22023'; END IF;
  next_reserved:=ledger.reserved_increase_minor-reservation.amount_minor;
  frozen:=CASE WHEN ledger.freeze_reason IS NOT NULL AND ledger.freeze_reason<>'OVER_LIMIT' THEN ledger.freeze_reason
   WHEN ledger.confirmed_total_minor+next_reserved>ledger.limit_minor THEN 'OVER_LIMIT' END;
  UPDATE public.budget_ledgers SET reserved_increase_minor=next_reserved,freeze_reason=frozen,version=version+1
   WHERE tenant_id=tenant AND id=ledger.id;
  INSERT INTO public.budget_adjustments(id,tenant_id,finalization_id,ledger_id,campaign_id,account_id,observation_id,
   confirmed_before,confirmed_after,reserved_before,reserved_after,budget_delta)
   VALUES(gen_random_uuid(),tenant,p_id,ledger.id,job.campaign_id,job.account_id,observation.id,
    ledger.confirmed_total_minor,ledger.confirmed_total_minor,ledger.reserved_increase_minor,next_reserved,0);
  buckets:=buckets+1;
 END LOOP;
 IF hold.id IS NOT NULL AND buckets=0 THEN RAISE EXCEPTION 'held bucket details missing' USING ERRCODE='22023'; END IF;
 UPDATE public.budget_reservations SET state='RELEASED' WHERE tenant_id=tenant AND job_id=p_job AND state='HELD';
 UPDATE public.job_budget_holds SET state='RELEASED' WHERE tenant_id=tenant AND job_id=p_job AND state='HELD';
 IF p_receipt IS NOT NULL THEN
  UPDATE public.target_execution_guards SET state='IDLE',job_id=NULL,lease_owner=NULL,lease_until=NULL
   WHERE tenant_id=tenant AND id=guard.id;
 END IF;
 result:=jsonb_build_object('finalization_id',p_id,'job_id',p_job,'kind','RELEASE','proof_kind',proof_kind,
  'job_version',job.version,'buckets',buckets,'verified_fact_id',NULL);
 INSERT INTO public.budget_job_finalizations(id,tenant_id,job_id,created_by,kind,proof_kind,receipt_id,result)
 VALUES(p_id,tenant,p_job,job.created_by,'RELEASE',proof_kind,p_receipt,result);
 RETURN jsonb_build_object('result',result,'created',true);
END $$;
REVOKE ALL ON FUNCTION access.budget_release(uuid,uuid,uuid,uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_release(uuid,uuid,uuid,uuid,uuid,uuid) TO app_login;

ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition','risk.assess',
 'budget.initialize','budget.reserve','idempotency.start','idempotency.complete','idempotency.recover',
 'execution.prepare','execution.result','budget.finalize'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK(object_type IN (
 'user','shop','member','role','project','conversation','message','job','platform_request',
 'oauth_state','authorization','sync_source','budget_ledger','idempotency_request','execution_order',
 'execution_receipt','budget_finalization'));
