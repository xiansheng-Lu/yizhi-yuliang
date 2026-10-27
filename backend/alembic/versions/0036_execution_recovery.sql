ALTER TABLE outbox_events ADD COLUMN not_before timestamptz NOT NULL DEFAULT now();
ALTER TABLE outbox_events DROP CONSTRAINT outbox_events_event_type_check;
ALTER TABLE outbox_events ADD CONSTRAINT outbox_events_event_type_check CHECK(event_type IN (
 'yuliang.system.identity_probe','yuliang.system.schedule_recovery','yuliang.system.execution_recovery',
 'yuliang.execute.guard','yuliang.platform.guard','yuliang.ai_media.guard'));
CREATE TABLE execution_recoveries (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,order_id uuid NOT NULL,attempt_id uuid NOT NULL,
 job_id uuid NOT NULL,actor_id uuid NOT NULL,membership_id uuid NOT NULL,
 state varchar(15) NOT NULL CHECK(state IN ('DUE','WAITING','DONE','BLOCKED_AUTH','MANUAL_REQUIRED')),
 outcome varchar(30) NOT NULL DEFAULT 'PENDING',generation integer NOT NULL DEFAULT 1 CHECK(generation>0),
 next_due timestamptz NOT NULL,checks integer NOT NULL DEFAULT 0 CHECK(checks>=0),
 current_event_id uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),result jsonb,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,attempt_id),UNIQUE(tenant_id,current_event_id),
 FOREIGN KEY(tenant_id,order_id,attempt_id) REFERENCES execution_attempts(tenant_id,order_id,id),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id),
 FOREIGN KEY(tenant_id,membership_id,actor_id) REFERENCES memberships(tenant_id,id,user_id),
 FOREIGN KEY(tenant_id,current_event_id) REFERENCES outbox_events(tenant_id,event_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE execution_recovery_events (
 event_id uuid PRIMARY KEY,tenant_id bigint NOT NULL,recovery_id uuid NOT NULL,generation integer NOT NULL,
 due_at timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,event_id),UNIQUE(tenant_id,recovery_id,generation),
 UNIQUE(tenant_id,recovery_id,event_id),
 FOREIGN KEY(tenant_id,recovery_id) REFERENCES execution_recoveries(tenant_id,id),
 FOREIGN KEY(tenant_id,event_id) REFERENCES outbox_events(tenant_id,event_id)
);
ALTER TABLE execution_recoveries ADD CONSTRAINT fk_current_recovery_event
 FOREIGN KEY(tenant_id,id,current_event_id) REFERENCES execution_recovery_events(tenant_id,recovery_id,event_id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE execution_recovery_evidence (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,recovery_id uuid NOT NULL,generation integer NOT NULL,
 order_id uuid NOT NULL,attempt_id uuid NOT NULL,job_id uuid NOT NULL,job_version bigint NOT NULL,
 guard_id uuid NOT NULL,fence bigint NOT NULL,lease_owner uuid NOT NULL,guard_state varchar(12) NOT NULL,
 outbound_id uuid,network_attempts integer NOT NULL CHECK(network_attempts>=0),
 kind varchar(20) NOT NULL CHECK(kind IN ('NOT_SENT','UNKNOWN','TERMINAL','STALE_GUARD','AUTH_BLOCKED','ERROR_BLOCKED')),
 evidence_level varchar(2) NOT NULL CHECK(evidence_level IN ('E0','E1')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,recovery_id,generation),
 FOREIGN KEY(tenant_id,recovery_id) REFERENCES execution_recoveries(tenant_id,id),
 FOREIGN KEY(tenant_id,order_id,attempt_id) REFERENCES execution_attempts(tenant_id,order_id,id),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id),
 FOREIGN KEY(tenant_id,guard_id) REFERENCES target_execution_guards(tenant_id,id),
 FOREIGN KEY(tenant_id,outbound_id) REFERENCES platform_requests(tenant_id,id),
 CHECK((kind='NOT_SENT')=(evidence_level='E1')),
 CHECK(kind<>'NOT_SENT' OR (network_attempts=0 AND guard_state='QUARANTINED'))
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['execution_recoveries','execution_recovery_events','execution_recovery_evidence'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;

CREATE FUNCTION access.execution_recovery_context(p_member uuid,p_user uuid,p_event uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); delivery public.execution_recovery_events;
 recovery public.execution_recoveries; item public.execution_orders; attempt public.execution_attempts;
 job public.command_jobs; claim public.target_guard_claims; guard public.target_execution_guards;
 root public.platform_requests; raw public.platform_raw_payloads; attempts integer; physical boolean; kind text;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT * INTO delivery FROM public.execution_recovery_events WHERE tenant_id=tenant AND event_id=p_event;
 SELECT * INTO recovery FROM public.execution_recoveries WHERE tenant_id=tenant AND id=delivery.recovery_id
  AND actor_id=p_user AND membership_id=p_member FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'recovery unavailable' USING ERRCODE='P0002'; END IF;
 PERFORM access.job_state_context(p_member,p_user,recovery.job_id,'CLAIM');
 IF recovery.state IN ('DONE','BLOCKED_AUTH','MANUAL_REQUIRED') OR delivery.generation<>recovery.generation THEN
  RETURN jsonb_build_object('existing',coalesce(recovery.result,jsonb_build_object('recovery_id',recovery.id,
   'state',recovery.state,'outcome','STALE_EVENT','attempt_id',recovery.attempt_id,
   'next_event_id',recovery.current_event_id,'next_due',recovery.next_due)));
 END IF;
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=recovery.job_id FOR UPDATE;
 SELECT * INTO item FROM public.execution_orders WHERE tenant_id=tenant AND id=recovery.order_id FOR UPDATE;
 SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=tenant AND id=recovery.attempt_id FOR UPDATE;
 SELECT * INTO claim FROM public.target_guard_claims WHERE tenant_id=tenant AND id=attempt.guard_claim_id;
 SELECT * INTO guard FROM public.target_execution_guards WHERE tenant_id=tenant AND id=claim.guard_id FOR UPDATE;
 SELECT * INTO root FROM public.platform_requests WHERE tenant_id=tenant AND id=attempt.outbound_id;
 SELECT count(*) INTO attempts FROM public.platform_raw_payloads WHERE tenant_id=tenant AND outbound_id=root.id AND phase='ATTEMPT';
 SELECT * INTO raw FROM public.platform_raw_payloads WHERE tenant_id=tenant AND outbound_id=root.id ORDER BY id DESC LIMIT 1;
 SELECT EXISTS(SELECT 1 FROM public.platform_raw_payloads WHERE tenant_id=tenant AND outbound_id=root.id
  AND (phase IN ('ATTEMPT','RESPONSE') OR dispatch IN ('UNKNOWN','RESPONDED','SENDING'))) INTO physical;
 IF job.status IN ('SUCCEEDED','FAILED') AND EXISTS(SELECT 1 FROM public.budget_job_finalizations done
  WHERE done.tenant_id=tenant AND done.job_id=job.id AND (
   EXISTS(SELECT 1 FROM public.verified_action_facts f WHERE f.id=done.verified_fact_id AND f.write_outbound_id=attempt.outbound_id)
   OR EXISTS(SELECT 1 FROM public.execution_receipts r WHERE r.tenant_id=tenant AND r.id=done.receipt_id AND r.attempt_id=attempt.id)
   OR EXISTS(SELECT 1 FROM public.execution_recovery_evidence e WHERE e.tenant_id=tenant AND e.id=done.recovery_proof_id AND e.attempt_id=attempt.id))) THEN
  kind:='TERMINAL';
 ELSIF recovery.next_due>clock_timestamp() THEN RETURN jsonb_build_object('deferred',recovery.next_due);
 ELSIF guard.job_id IS DISTINCT FROM job.id OR guard.fence IS DISTINCT FROM claim.fence
  OR guard.lease_owner IS DISTINCT FROM claim.lease_owner OR guard.state='IDLE'
  OR item.current_attempt<>attempt.attempt_no THEN kind:='STALE_GUARD';
 ELSIF guard.state='LEASED' AND guard.lease_until>clock_timestamp() THEN
  RETURN jsonb_build_object('deferred',guard.lease_until);
 ELSE
  -- 取排他行锁并撤销LEASED资格，再读取网络事实；原ATTEMPT门持有共享锁，二者在线性点串行。
  UPDATE public.target_execution_guards SET state='QUARANTINED' WHERE tenant_id=tenant AND id=guard.id;
  guard.state:='QUARANTINED';
  -- 更新守卫后再采样，不能用先前可能与ATTEMPT提交竞争的0计数发E1。
  SELECT count(*) INTO attempts FROM public.platform_raw_payloads WHERE tenant_id=tenant AND outbound_id=root.id AND phase='ATTEMPT';
  SELECT EXISTS(SELECT 1 FROM public.platform_raw_payloads WHERE tenant_id=tenant AND outbound_id=root.id
   AND (phase IN ('ATTEMPT','RESPONSE') OR dispatch IN ('UNKNOWN','RESPONDED','SENDING'))) INTO physical;
  SELECT * INTO raw FROM public.platform_raw_payloads WHERE tenant_id=tenant AND outbound_id=root.id ORDER BY id DESC LIMIT 1;
  kind:=CASE WHEN NOT physical AND job.status='EXECUTING' AND item.status IN ('EXECUTING','UNKNOWN')
   AND attempt.state IN ('SENT','UNKNOWN') AND job.document->'execution_receipt'='null'::jsonb
   AND (root.id IS NULL OR (root.execution_id=item.id AND root.execution_attempt_id=attempt.id AND root.user_id=item.created_by))
   THEN 'NOT_SENT' ELSE 'UNKNOWN' END;
 END IF;
 RETURN jsonb_build_object('recovery',to_jsonb(recovery),'job',to_jsonb(job),'order',to_jsonb(item),
  'attempt',to_jsonb(attempt),'claim',to_jsonb(claim),'guard',to_jsonb(guard),'root',
  CASE WHEN root.id IS NOT NULL THEN to_jsonb(root) END,'raw',CASE WHEN raw.id IS NOT NULL THEN to_jsonb(raw) END,
  'networkAttempts',attempts,'kind',kind);
END $$;
REVOKE ALL ON FUNCTION access.execution_recovery_context(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_recovery_context(uuid,uuid,uuid) TO app_login;

CREATE FUNCTION access.execution_recovery_observe(p_member uuid,p_user uuid,p_event uuid,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); ctx jsonb; proof public.execution_recovery_evidence;
BEGIN
 ctx:=access.execution_recovery_context(p_member,p_user,p_event);
 IF ctx?'existing' OR ctx?'deferred' THEN RETURN ctx; END IF;
 SELECT * INTO proof FROM public.execution_recovery_evidence WHERE tenant_id=tenant
  AND recovery_id=(ctx#>>'{recovery,id}')::uuid AND generation=(ctx#>>'{recovery,generation}')::integer;
 IF FOUND THEN RETURN jsonb_build_object('context',ctx,'proof',to_jsonb(proof)); END IF;
 INSERT INTO public.execution_recovery_evidence(id,tenant_id,recovery_id,generation,order_id,attempt_id,
  job_id,job_version,guard_id,fence,lease_owner,guard_state,outbound_id,network_attempts,kind,evidence_level)
 VALUES(p_id,tenant,(ctx#>>'{recovery,id}')::uuid,(ctx#>>'{recovery,generation}')::integer,
  (ctx#>>'{order,id}')::uuid,(ctx#>>'{attempt,id}')::uuid,(ctx#>>'{job,id}')::uuid,
  (ctx#>>'{job,version}')::bigint,(ctx#>>'{guard,id}')::uuid,(ctx#>>'{claim,fence}')::bigint,
  (ctx#>>'{claim,lease_owner}')::uuid,ctx#>>'{guard,state}',(ctx#>>'{root,id}')::uuid,
  (ctx->>'networkAttempts')::integer,ctx->>'kind',CASE WHEN ctx->>'kind'='NOT_SENT' THEN 'E1' ELSE 'E0' END)
 RETURNING * INTO proof;
 RETURN jsonb_build_object('context',ctx,'proof',to_jsonb(proof));
END $$;
REVOKE ALL ON FUNCTION access.execution_recovery_observe(uuid,uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_recovery_observe(uuid,uuid,uuid,uuid) TO app_login;

ALTER TABLE budget_job_finalizations ADD COLUMN recovery_proof_id uuid,
 ADD CONSTRAINT fk_finalization_recovery FOREIGN KEY(tenant_id,recovery_proof_id) REFERENCES execution_recovery_evidence(tenant_id,id),
 ADD CONSTRAINT ck_recovery_proof CHECK((proof_kind='RECOVERY_NOT_SENT')=(recovery_proof_id IS NOT NULL));
ALTER TABLE budget_job_finalizations DROP CONSTRAINT budget_job_finalizations_proof_kind_check;
ALTER TABLE budget_job_finalizations ADD CONSTRAINT budget_job_finalizations_proof_kind_check CHECK(proof_kind IN (
 'VERIFIED_MATCH','UNCLAIMED_TERMINAL','PLATFORM_REJECTION','RECOVERY_NOT_SENT'));

CREATE FUNCTION access.execution_recovery_not_sent(p_member uuid,p_user uuid,p_proof uuid,p_document jsonb,p_request uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); proof public.execution_recovery_evidence;
 job public.command_jobs; attempt public.execution_attempts; guard public.target_execution_guards; digest text;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT * INTO proof FROM public.execution_recovery_evidence WHERE tenant_id=tenant AND id=p_proof
  AND kind='NOT_SENT' AND evidence_level='E1' AND network_attempts=0;
 IF NOT FOUND THEN RAISE EXCEPTION 'not-sent proof unavailable' USING ERRCODE='P0002'; END IF;
 PERFORM access.job_state_context(p_member,p_user,proof.job_id,'CLAIM');
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=proof.job_id FOR UPDATE;
 SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=tenant AND id=proof.attempt_id FOR UPDATE;
 SELECT * INTO guard FROM public.target_execution_guards WHERE tenant_id=tenant AND id=proof.guard_id FOR UPDATE;
 IF job.status<>'EXECUTING' OR job.version<>proof.job_version OR guard.state<>'QUARANTINED'
  OR guard.job_id IS DISTINCT FROM job.id OR guard.fence<>proof.fence OR guard.lease_owner<>proof.lease_owner
  OR EXISTS(SELECT 1 FROM public.platform_raw_payloads raw WHERE raw.tenant_id=tenant AND raw.outbound_id=attempt.outbound_id
   AND (raw.phase IN ('ATTEMPT','RESPONSE') OR raw.dispatch IN ('SENDING','RESPONDED','UNKNOWN'))) THEN
  RAISE EXCEPTION 'physical dispatch proof changed' USING ERRCODE='40001'; END IF;
 IF p_document->'execution_receipt' IS DISTINCT FROM 'null'::jsonb
  OR p_document#>>'{error,code}' IS DISTINCT FROM 'NOT_SENT' OR p_document#>>'{error,retryable}' IS DISTINCT FROM 'false'
  OR p_document->'risk_summary' IS DISTINCT FROM job.document->'risk_summary'
  OR p_document->'approval' IS DISTINCT FROM job.document->'approval' THEN
  RAISE EXCEPTION 'system E1 document required' USING ERRCODE='22023'; END IF;
 digest:=encode(sha256(convert_to('recovery-not-sent|'||p_proof::text,'UTF8')),'hex');
 PERFORM access.job_apply_transition(p_member,p_user,job.id,job.version,'CONFIRMED_NOT_EXECUTED',p_document,p_request,digest);
 UPDATE public.execution_orders SET status='FAILED',version=version+1,updated_at=clock_timestamp()
  WHERE tenant_id=tenant AND id=proof.order_id;
 UPDATE public.execution_attempts SET state='REJECTED',finished_at=clock_timestamp(),version=version+1
  WHERE tenant_id=tenant AND id=proof.attempt_id;
END $$;
REVOKE ALL ON FUNCTION access.execution_recovery_not_sent(uuid,uuid,uuid,jsonb,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_recovery_not_sent(uuid,uuid,uuid,jsonb,uuid) TO app_login;

CREATE FUNCTION access.budget_release_recovery(p_member uuid,p_user uuid,p_job uuid,p_proof uuid,p_id uuid,p_request uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); proof public.execution_recovery_evidence;
 job public.command_jobs; guard public.target_execution_guards; attempt public.execution_attempts;
 finished public.budget_job_finalizations; ledger public.budget_ledgers; reservation public.budget_reservations;
 hold public.job_budget_holds; observation public.budget_observations; held_sum numeric; remaining bigint;
 frozen text; buckets integer:=0; result jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 PERFORM access.job_state_context(p_member,p_user,p_job,'CLAIM');
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job FOR UPDATE;
 SELECT * INTO finished FROM public.budget_job_finalizations WHERE tenant_id=tenant AND job_id=p_job;
 IF FOUND THEN
  IF finished.kind<>'RELEASE' OR finished.recovery_proof_id IS DISTINCT FROM p_proof THEN
   RAISE EXCEPTION 'finalization proof differs' USING ERRCODE='23505'; END IF;
  RETURN jsonb_build_object('result',finished.result,'created',false);
 END IF;
 SELECT * INTO proof FROM public.execution_recovery_evidence WHERE tenant_id=tenant AND id=p_proof AND job_id=p_job
  AND kind='NOT_SENT' AND evidence_level='E1' AND network_attempts=0;
 IF NOT FOUND OR job.status<>'FAILED' OR job.version<>proof.job_version+1
  OR job.document#>>'{error,code}' IS DISTINCT FROM 'NOT_SENT'
  OR job.document->'execution_receipt' IS DISTINCT FROM 'null'::jsonb
  OR NOT EXISTS(SELECT 1 FROM public.job_events e WHERE e.tenant_id=tenant AND e.job_id=p_job
   AND e.job_version=job.version AND e.control_event='CONFIRMED_NOT_EXECUTED'
   AND e.proof_digest=encode(sha256(convert_to('recovery-not-sent|'||p_proof::text,'UTF8')),'hex')) THEN
  RAISE EXCEPTION 'durable recovered E1 terminal required' USING ERRCODE='22023'; END IF;
 SELECT * INTO guard FROM public.target_execution_guards WHERE tenant_id=tenant AND id=proof.guard_id FOR UPDATE;
 SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=tenant AND id=proof.attempt_id;
 IF guard.state<>'QUARANTINED' OR guard.job_id IS DISTINCT FROM p_job OR guard.fence<>proof.fence
  OR guard.lease_owner<>proof.lease_owner OR EXISTS(SELECT 1 FROM public.platform_raw_payloads raw
   WHERE raw.tenant_id=tenant AND raw.outbound_id=attempt.outbound_id
    AND (raw.phase IN ('ATTEMPT','RESPONSE') OR raw.dispatch IN ('SENDING','RESPONDED','UNKNOWN'))) THEN
  RAISE EXCEPTION 'recovery dispatch qualification changed' USING ERRCODE='40001'; END IF;
 SELECT * INTO hold FROM public.job_budget_holds WHERE tenant_id=tenant AND job_id=p_job FOR UPDATE;
 IF hold.id IS NOT NULL AND (hold.state<>'HELD' OR hold.intent_hash<>job.intent_hash) THEN
  RAISE EXCEPTION 'held journal differs' USING ERRCODE='22023'; END IF;
 FOR ledger IN SELECT l.* FROM public.budget_ledgers l JOIN public.budget_reservations r
  ON (r.tenant_id,r.ledger_id)=(l.tenant_id,l.id) WHERE r.tenant_id=tenant AND r.job_id=p_job ORDER BY l.id FOR UPDATE OF l LOOP
  SELECT * INTO reservation FROM public.budget_reservations WHERE tenant_id=tenant AND job_id=p_job AND ledger_id=ledger.id FOR UPDATE;
  SELECT coalesce(sum(amount_minor),0) INTO held_sum FROM public.budget_reservations WHERE tenant_id=tenant
   AND ledger_id=ledger.id AND state='HELD';
  SELECT * INTO observation FROM public.budget_observations WHERE tenant_id=tenant AND ledger_id=ledger.id
   ORDER BY recorded_at DESC,id DESC LIMIT 1;
  IF NOT FOUND OR ledger.confirmed_total_minor IS NULL OR held_sum<>ledger.reserved_increase_minor
   OR reservation.state<>'HELD' OR reservation.hold_id IS DISTINCT FROM hold.id THEN
   RAISE EXCEPTION 'reservation basis unknown' USING ERRCODE='22023'; END IF;
  remaining:=ledger.reserved_increase_minor-reservation.amount_minor;
  frozen:=CASE WHEN ledger.freeze_reason IS NOT NULL AND ledger.freeze_reason<>'OVER_LIMIT' THEN ledger.freeze_reason
   WHEN ledger.confirmed_total_minor+remaining>ledger.limit_minor THEN 'OVER_LIMIT' END;
  UPDATE public.budget_ledgers SET reserved_increase_minor=remaining,freeze_reason=frozen,version=version+1
   WHERE tenant_id=tenant AND id=ledger.id;
  INSERT INTO public.budget_adjustments(id,tenant_id,finalization_id,ledger_id,campaign_id,account_id,observation_id,
   confirmed_before,confirmed_after,reserved_before,reserved_after,budget_delta)
  VALUES(gen_random_uuid(),tenant,p_id,ledger.id,job.campaign_id,job.account_id,observation.id,
   ledger.confirmed_total_minor,ledger.confirmed_total_minor,ledger.reserved_increase_minor,remaining,0);
  PERFORM access.budget_refresh_alerts(ledger.id,observation.id,p_request);
  buckets:=buckets+1;
 END LOOP;
 IF hold.id IS NOT NULL AND buckets=0 THEN RAISE EXCEPTION 'held details missing' USING ERRCODE='22023'; END IF;
 UPDATE public.budget_reservations SET state='RELEASED' WHERE tenant_id=tenant AND job_id=p_job AND state='HELD';
 UPDATE public.job_budget_holds SET state='RELEASED' WHERE tenant_id=tenant AND job_id=p_job AND state='HELD';
 UPDATE public.target_execution_guards SET state='IDLE',job_id=NULL,lease_owner=NULL,lease_until=NULL
  WHERE tenant_id=tenant AND id=guard.id;
 result:=jsonb_build_object('finalization_id',p_id,'job_id',p_job,'kind','RELEASE','proof_kind','RECOVERY_NOT_SENT',
  'job_version',job.version,'buckets',buckets,'verified_fact_id',NULL);
 INSERT INTO public.budget_job_finalizations(id,tenant_id,job_id,created_by,kind,proof_kind,recovery_proof_id,result)
 VALUES(p_id,tenant,p_job,job.created_by,'RELEASE','RECOVERY_NOT_SENT',p_proof,result);
 RETURN jsonb_build_object('result',result,'created',true);
END $$;
REVOKE ALL ON FUNCTION access.budget_release_recovery(uuid,uuid,uuid,uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_release_recovery(uuid,uuid,uuid,uuid,uuid,uuid) TO app_login;

CREATE FUNCTION access.execution_recovery_next(p_id uuid,p_due timestamptz,p_state text,p_outcome text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE recovery public.execution_recoveries; identity uuid:=gen_random_uuid(); payload jsonb;
BEGIN
 SELECT * INTO recovery FROM public.execution_recoveries WHERE id=p_id FOR UPDATE;
 payload:=jsonb_build_object('event_id',identity,'tenant_id',recovery.tenant_id,'job_id',recovery.job_id,
  'membership_id',recovery.membership_id,'user_id',recovery.actor_id);
 INSERT INTO public.outbox_events(event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload,not_before)
 VALUES(identity,recovery.tenant_id,'job',recovery.job_id::text,'yuliang.system.execution_recovery',payload,p_due);
 INSERT INTO public.execution_recovery_events(event_id,tenant_id,recovery_id,generation,due_at)
 VALUES(identity,recovery.tenant_id,recovery.id,recovery.generation+1,p_due);
 UPDATE public.execution_recoveries SET current_event_id=identity,generation=generation+1,next_due=p_due,
  state=p_state,outcome=p_outcome,updated_at=clock_timestamp() WHERE id=p_id;
 INSERT INTO public.scheduled_tasks(tenant_id,kind,business_key,payload,due_at,source_event_id,state,attempts)
 VALUES(recovery.tenant_id,'execution.recover',recovery.attempt_id::text,payload,p_due,identity,'RESCHEDULED',recovery.checks)
 ON CONFLICT(tenant_id,kind,business_key) DO UPDATE SET payload=EXCLUDED.payload,due_at=EXCLUDED.due_at,
  source_event_id=EXCLUDED.source_event_id,state='RESCHEDULED',lease_owner=NULL,lease_until=NULL,attempts=recovery.checks;
 RETURN identity;
END $$;
REVOKE ALL ON FUNCTION access.execution_recovery_next(uuid,timestamptz,text,text) FROM PUBLIC,app_login,app_auth;

CREATE FUNCTION access.execution_recovery_finish(p_member uuid,p_user uuid,p_event uuid,p_proof uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); ctx jsonb; recovery public.execution_recoveries;
 proof public.execution_recovery_evidence; state_value text; outcome_value text; snapshot jsonb; next_id uuid; due timestamptz;
BEGIN
 ctx:=access.execution_recovery_context(p_member,p_user,p_event);
 IF ctx?'existing' THEN RETURN ctx->'existing'; END IF;
 IF ctx?'deferred' THEN RAISE EXCEPTION 'recovery not due' USING ERRCODE='55P03'; END IF;
 SELECT * INTO recovery FROM public.execution_recoveries WHERE tenant_id=tenant AND id=(ctx#>>'{recovery,id}')::uuid;
 SELECT * INTO proof FROM public.execution_recovery_evidence WHERE tenant_id=tenant AND id=p_proof
  AND recovery_id=recovery.id AND generation=recovery.generation;
 IF NOT FOUND THEN RAISE EXCEPTION 'current recovery observation required' USING ERRCODE='22023'; END IF;
 IF ctx->>'kind'='TERMINAL' THEN
  state_value:='DONE'; outcome_value:=CASE WHEN proof.kind='NOT_SENT' THEN 'NOT_SENT' ELSE 'TERMINAL' END;
 ELSIF ctx->>'kind'='STALE_GUARD' THEN state_value:='MANUAL_REQUIRED'; outcome_value:='STALE_GUARD';
 ELSIF recovery.checks+1>=8 THEN state_value:='MANUAL_REQUIRED'; outcome_value:='RECONCILIATION_UNRESOLVED';
 ELSE
  state_value:='WAITING'; outcome_value:=CASE WHEN ctx#>>'{job,status}'='VERIFYING' THEN 'VERIFYING' ELSE 'UNKNOWN' END;
 END IF;
 UPDATE public.execution_recoveries SET checks=checks+1,state=state_value,outcome=outcome_value,updated_at=clock_timestamp()
  WHERE tenant_id=tenant AND id=recovery.id;
 IF state_value='WAITING' THEN
  due:=clock_timestamp()+make_interval(secs=>least(600,30*(2^least(recovery.checks,4))::integer));
  next_id:=access.execution_recovery_next(recovery.id,due,state_value,outcome_value);
 ELSE
  UPDATE public.scheduled_tasks SET state=CASE WHEN state_value='DONE' THEN 'DONE' ELSE 'FAILED' END,
   lease_owner=NULL,lease_until=NULL,last_error=CASE WHEN state_value<>'DONE' THEN outcome_value END
   WHERE tenant_id=tenant AND kind='execution.recover' AND business_key=recovery.attempt_id::text;
 END IF;
 snapshot:=jsonb_build_object('recovery_id',recovery.id,'attempt_id',recovery.attempt_id,'state',state_value,'outcome',outcome_value,
  'proof_id',proof.id,'evidence_level',proof.evidence_level,'network_attempts',proof.network_attempts,
  'job_status',ctx#>>'{job,status}','job_version',(ctx#>>'{job,version}')::bigint,
  'next_event_id',next_id,'next_due',due);
 UPDATE public.execution_recoveries SET result=snapshot WHERE tenant_id=tenant AND id=recovery.id;
 RETURN snapshot;
END $$;
REVOKE ALL ON FUNCTION access.execution_recovery_finish(uuid,uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_recovery_finish(uuid,uuid,uuid,uuid) TO app_login;

CREATE FUNCTION access.execution_recovery_publish_claim(p_member uuid,p_user uuid,p_event uuid,p_owner uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); recovery public.execution_recoveries; delivery public.execution_recovery_events;
 event public.outbox_events;
BEGIN
 SELECT r.* INTO recovery FROM public.execution_recovery_events d JOIN public.execution_recoveries r
  ON (r.tenant_id,r.id,r.current_event_id)=(d.tenant_id,d.recovery_id,d.event_id)
  WHERE d.tenant_id=tenant AND d.event_id=p_event AND r.actor_id=p_user AND r.membership_id=p_member
   AND r.state IN ('DUE','WAITING');
 IF NOT FOUND THEN RETURN NULL; END IF;
 PERFORM access.job_state_context(p_member,p_user,recovery.job_id,'CLAIM');
 SELECT * INTO delivery FROM public.execution_recovery_events WHERE tenant_id=tenant AND event_id=p_event;
 UPDATE public.outbox_events SET lease_owner=p_owner,lease_until=clock_timestamp()+interval '30 seconds',
  attempts=attempts+1,last_error_code=NULL WHERE tenant_id=tenant AND event_id=p_event
   AND event_type='yuliang.system.execution_recovery' AND state='PENDING'
   AND (lease_until IS NULL OR lease_until<=clock_timestamp()) RETURNING * INTO event;
 IF NOT FOUND THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('event_type',event.event_type,'payload',event.payload,'due_at',delivery.due_at);
END $$;
REVOKE ALL ON FUNCTION access.execution_recovery_publish_claim(uuid,uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_recovery_publish_claim(uuid,uuid,uuid,uuid) TO app_login;

CREATE FUNCTION access.execution_recovery_blocked(p_member uuid,p_user uuid,p_event uuid,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); recovery public.execution_recoveries;
 attempt public.execution_attempts; claim public.target_guard_claims; guard public.target_execution_guards;
 job public.command_jobs; proof public.execution_recovery_evidence; snapshot jsonb; new_state text;
BEGIN
 IF p_user IS DISTINCT FROM public.current_actor_id() OR p_reason NOT IN ('AUTH_REVOKED','INTEGRITY_REQUIRED') THEN
  RAISE EXCEPTION 'controlled recovery block required' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT r.* INTO recovery FROM public.execution_recovery_events d JOIN public.execution_recoveries r
  ON (r.tenant_id,r.id)=(d.tenant_id,d.recovery_id) WHERE d.tenant_id=tenant AND d.event_id=p_event
   AND r.actor_id=p_user AND r.membership_id=p_member FOR UPDATE OF r;
 IF NOT FOUND THEN RAISE EXCEPTION 'original event owner required' USING ERRCODE='P0002'; END IF;
 IF recovery.state='DONE' THEN RETURN jsonb_build_object('recovery_id',recovery.id,'attempt_id',recovery.attempt_id,
  'state','DONE','outcome',p_reason); END IF;
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=recovery.job_id FOR UPDATE;
 SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=tenant AND id=recovery.attempt_id;
 SELECT * INTO claim FROM public.target_guard_claims WHERE tenant_id=tenant AND id=attempt.guard_claim_id;
 SELECT * INTO guard FROM public.target_execution_guards WHERE tenant_id=tenant AND id=claim.guard_id FOR UPDATE;
 IF guard.state='LEASED' AND guard.lease_until<=clock_timestamp() AND guard.job_id=job.id
  AND guard.fence=claim.fence AND guard.lease_owner=claim.lease_owner THEN
  UPDATE public.target_execution_guards SET state='QUARANTINED' WHERE tenant_id=tenant AND id=guard.id;
  guard.state:='QUARANTINED';
 END IF;
 INSERT INTO public.execution_recovery_evidence(id,tenant_id,recovery_id,generation,order_id,attempt_id,
  job_id,job_version,guard_id,fence,lease_owner,guard_state,outbound_id,network_attempts,kind,evidence_level)
 VALUES(gen_random_uuid(),tenant,recovery.id,recovery.generation,recovery.order_id,attempt.id,job.id,job.version,
  guard.id,claim.fence,claim.lease_owner,guard.state,
  (SELECT id FROM public.platform_requests WHERE tenant_id=tenant AND id=attempt.outbound_id),
  (SELECT count(*) FROM public.platform_raw_payloads WHERE tenant_id=tenant AND outbound_id=attempt.outbound_id AND phase='ATTEMPT'),
  CASE WHEN p_reason='AUTH_REVOKED' THEN 'AUTH_BLOCKED' ELSE 'ERROR_BLOCKED' END,'E0')
 ON CONFLICT(tenant_id,recovery_id,generation) DO NOTHING;
 SELECT * INTO proof FROM public.execution_recovery_evidence WHERE tenant_id=tenant AND recovery_id=recovery.id AND generation=recovery.generation;
 new_state:=CASE WHEN p_reason='AUTH_REVOKED' THEN 'BLOCKED_AUTH' ELSE 'MANUAL_REQUIRED' END;
 snapshot:=jsonb_build_object('recovery_id',recovery.id,'attempt_id',attempt.id,'state',new_state,'outcome',p_reason,
  'proof_id',proof.id,'evidence_level','E0','network_attempts',proof.network_attempts);
 UPDATE public.execution_recoveries SET state=new_state,outcome=p_reason,result=snapshot,updated_at=clock_timestamp()
  WHERE tenant_id=tenant AND id=recovery.id;
 UPDATE public.scheduled_tasks SET state='FAILED',last_error=p_reason,lease_owner=NULL,lease_until=NULL
  WHERE tenant_id=tenant AND kind='execution.recover' AND business_key=attempt.id::text;
 -- 身份已撤销时只留SYSTEM/E0操作记录，不伪造有效用户审计，也不动job/B/R。
 RETURN snapshot;
END $$;
REVOKE ALL ON FUNCTION access.execution_recovery_blocked(uuid,uuid,uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_recovery_blocked(uuid,uuid,uuid,text) TO app_login;

CREATE FUNCTION access.execution_recovery_resume(p_member uuid,p_user uuid,p_event uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); recovery public.execution_recoveries; new_event uuid; due timestamptz:=clock_timestamp(); snapshot jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT r.* INTO recovery FROM public.execution_recovery_events d JOIN public.execution_recoveries r
  ON (r.tenant_id,r.id)=(d.tenant_id,d.recovery_id) WHERE d.tenant_id=tenant AND d.event_id=p_event
   AND r.actor_id=p_user AND r.membership_id=p_member FOR UPDATE OF r;
 IF NOT FOUND THEN RAISE EXCEPTION 'recovery unavailable' USING ERRCODE='P0002'; END IF;
 PERFORM access.job_state_context(p_member,p_user,recovery.job_id,'CLAIM');
 IF recovery.state NOT IN ('BLOCKED_AUTH','MANUAL_REQUIRED') THEN
  RAISE EXCEPTION 'only blocked recovery may resume' USING ERRCODE='40001'; END IF;
 UPDATE public.execution_recoveries SET checks=0 WHERE tenant_id=tenant AND id=recovery.id;
 new_event:=access.execution_recovery_next(recovery.id,due,'DUE','RESUMED');
 snapshot:=jsonb_build_object('recovery_id',recovery.id,'attempt_id',recovery.attempt_id,'state','DUE',
  'outcome','RESUMED','next_event_id',new_event,'next_due',due);
 UPDATE public.execution_recoveries SET result=snapshot WHERE tenant_id=tenant AND id=recovery.id;
 RETURN snapshot;
END $$;
REVOKE ALL ON FUNCTION access.execution_recovery_resume(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_recovery_resume(uuid,uuid,uuid) TO app_login;

CREATE FUNCTION access.execution_recovery_manager(p_member uuid,p_user uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); snapshot jsonb;
BEGIN
 PERFORM access.budget_governance_scope(p_member,p_user);
 SELECT jsonb_build_object('pending',(SELECT count(*) FROM public.execution_recoveries WHERE tenant_id=tenant AND state<>'DONE'),
  'items',coalesce((SELECT jsonb_agg(entry ORDER BY priority,updated_at DESC) FROM(
   SELECT jsonb_build_object('recoveryId',id,'attemptId',attempt_id,'state',state,'outcome',outcome,'nextDue',next_due) AS entry,
    CASE WHEN state IN ('BLOCKED_AUTH','MANUAL_REQUIRED') THEN 0 ELSE 1 END AS priority,updated_at
   FROM public.execution_recoveries WHERE tenant_id=tenant AND state<>'DONE' ORDER BY priority,updated_at DESC LIMIT 100
  ) bounded),'[]'::jsonb)) INTO snapshot;
 RETURN snapshot;
END $$;
REVOKE ALL ON FUNCTION access.execution_recovery_manager(uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.execution_recovery_manager(uuid,uuid) TO app_login;
CREATE POLICY private_actor ON execution_recoveries FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id());
CREATE POLICY private_actor ON execution_recovery_events FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM execution_recoveries r
  WHERE r.tenant_id=execution_recovery_events.tenant_id AND r.id=execution_recovery_events.recovery_id));
CREATE POLICY private_actor ON execution_recovery_evidence FOR SELECT TO app_login
 USING(tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM execution_recoveries r
  WHERE r.tenant_id=execution_recovery_evidence.tenant_id AND r.id=execution_recovery_evidence.recovery_id));

CREATE FUNCTION access.execution_recovery_schedule(p_attempt uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE attempt public.execution_attempts; item public.execution_orders; claim public.target_guard_claims;
 recovery public.execution_recoveries; event_id uuid; payload jsonb;
BEGIN
 SELECT * INTO attempt FROM public.execution_attempts WHERE id=p_attempt;
 SELECT * INTO item FROM public.execution_orders WHERE tenant_id=attempt.tenant_id AND id=attempt.order_id;
 IF item.biz_type<>'COMMAND_JOB' THEN RETURN NULL; END IF;
 SELECT * INTO recovery FROM public.execution_recoveries WHERE tenant_id=item.tenant_id AND attempt_id=p_attempt;
 IF FOUND THEN RETURN recovery.id; END IF;
 SELECT * INTO claim FROM public.target_guard_claims WHERE tenant_id=item.tenant_id AND id=attempt.guard_claim_id;
 event_id:=gen_random_uuid();
 payload:=jsonb_build_object('event_id',event_id,'tenant_id',item.tenant_id,'job_id',item.command_job_id,
  'membership_id',item.membership_id,'user_id',item.created_by);
 INSERT INTO public.outbox_events(event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload,not_before)
 VALUES(event_id,item.tenant_id,'job',item.command_job_id::text,'yuliang.system.execution_recovery',payload,claim.lease_until);
 INSERT INTO public.execution_recoveries(id,tenant_id,order_id,attempt_id,job_id,actor_id,membership_id,state,next_due,current_event_id)
 VALUES(gen_random_uuid(),item.tenant_id,item.id,p_attempt,item.command_job_id,item.created_by,item.membership_id,
  'DUE',claim.lease_until,event_id) RETURNING * INTO recovery;
 INSERT INTO public.execution_recovery_events(event_id,tenant_id,recovery_id,generation,due_at)
 VALUES(event_id,item.tenant_id,recovery.id,1,claim.lease_until);
 INSERT INTO public.scheduled_tasks(tenant_id,kind,business_key,payload,due_at,source_event_id)
 VALUES(item.tenant_id,'execution.recover',p_attempt::text,payload,claim.lease_until,event_id);
 RETURN recovery.id;
END $$;
REVOKE ALL ON FUNCTION access.execution_recovery_schedule(uuid) FROM PUBLIC,app_login,app_auth;
CREATE FUNCTION public.execution_recovery_register() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ BEGIN
 PERFORM access.execution_recovery_schedule(NEW.id); RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.execution_recovery_register() FROM PUBLIC,app_login,app_auth;
CREATE TRIGGER execution_recovery_register AFTER INSERT ON execution_attempts
 FOR EACH ROW EXECUTE FUNCTION public.execution_recovery_register();
DO $$ DECLARE item record; BEGIN
 FOR item IN SELECT a.id FROM execution_attempts a JOIN execution_orders o ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id)
  WHERE o.biz_type='COMMAND_JOB' AND o.status IN ('EXECUTING','VERIFYING','UNKNOWN') LOOP
  PERFORM access.execution_recovery_schedule(item.id);
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION access.outbox_claim(p_event uuid,p_owner uuid)
RETURNS TABLE(event_type varchar,payload jsonb)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 UPDATE public.outbox_events e SET lease_owner=p_owner,lease_until=statement_timestamp()+interval '30 seconds',
 attempts=attempts+1,last_error_code=NULL WHERE e.event_id=p_event AND e.tenant_id=public.current_tenant_id()
  AND e.state='PENDING' AND e.not_before<=statement_timestamp()
  AND (e.lease_until IS NULL OR e.lease_until<=statement_timestamp()) RETURNING e.event_type,e.payload;
$$;
CREATE OR REPLACE FUNCTION access.outbox_reopen_recovery(p_event uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE changed uuid;
BEGIN
 UPDATE public.outbox_events e SET state='PUBLISHED',published_at=coalesce(e.published_at,statement_timestamp()),
  lease_owner=NULL,lease_until=NULL,last_error_code=NULL WHERE e.event_id=p_event AND e.tenant_id=public.current_tenant_id()
  AND e.state='PENDING' AND e.event_type IN ('yuliang.system.schedule_recovery','yuliang.system.execution_recovery')
  AND EXISTS(SELECT 1 FROM public.consumer_inbox i WHERE i.event_id=e.event_id AND i.processed_at IS NOT NULL
   AND i.consumer=CASE e.event_type WHEN 'yuliang.system.execution_recovery' THEN 'execution.recovery' ELSE 'system.recovery' END);
 IF FOUND THEN RETURN false; END IF;
 UPDATE public.outbox_events e SET state='PENDING',published_at=NULL,lease_owner=NULL,lease_until=NULL,
  last_error_code='RECOVERY_REQUIRED' WHERE e.event_id=p_event AND e.tenant_id=public.current_tenant_id()
  AND e.event_type IN ('yuliang.system.schedule_recovery','yuliang.system.execution_recovery')
  AND CASE WHEN e.event_type='yuliang.system.execution_recovery'
   THEN greatest(coalesce(e.published_at,e.created_at),e.not_before)
   ELSE coalesce(e.published_at,e.created_at) END<statement_timestamp()-interval '30 seconds'
  AND (e.lease_until IS NULL OR e.lease_until<=statement_timestamp())
  AND NOT EXISTS(SELECT 1 FROM public.consumer_inbox i WHERE i.event_id=e.event_id AND i.processed_at IS NOT NULL
   AND i.consumer=CASE e.event_type WHEN 'yuliang.system.execution_recovery' THEN 'execution.recovery' ELSE 'system.recovery' END)
 RETURNING e.event_id INTO changed;
 RETURN changed IS NOT NULL;
END $$;
CREATE OR REPLACE FUNCTION access.outbox_candidates(p_limit integer)
RETURNS TABLE(event_id uuid,event_type varchar)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'batch size invalid' USING ERRCODE='22023'; END IF;
 RETURN QUERY SELECT e.event_id,e.event_type FROM public.outbox_events e WHERE e.tenant_id=public.current_tenant_id()
  AND e.not_before<=statement_timestamp() AND (e.state='PENDING' OR (
   e.event_type IN ('yuliang.system.schedule_recovery','yuliang.system.execution_recovery')
   AND CASE WHEN e.event_type='yuliang.system.execution_recovery'
    THEN greatest(e.published_at,e.not_before) ELSE e.published_at END<statement_timestamp()-interval '30 seconds'
   AND NOT EXISTS(SELECT 1 FROM public.consumer_inbox i WHERE i.event_id=e.event_id AND i.processed_at IS NOT NULL
    AND i.consumer=CASE e.event_type WHEN 'yuliang.system.execution_recovery' THEN 'execution.recovery' ELSE 'system.recovery' END)))
  ORDER BY e.id LIMIT p_limit;
END $$;


ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition','risk.assess',
 'budget.initialize','budget.reserve','idempotency.start','idempotency.complete','idempotency.recover',
 'execution.prepare','execution.result','budget.finalize','budget.observe','budget.resolve','execution.recover'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK(object_type IN (
 'user','shop','member','role','project','conversation','message','job','platform_request',
 'oauth_state','authorization','sync_source','budget_ledger','idempotency_request','execution_order',
 'execution_receipt','budget_finalization','budget_discrepancy','execution_recovery'));


-- 稳定执行出站键不得省略执行关联而绕过门禁。
CREATE OR REPLACE FUNCTION public.execution_root_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item public.execution_orders; attempt public.execution_attempts; given text;
BEGIN
 IF NEW.execution_id IS NULL THEN
  IF EXISTS(SELECT 1 FROM public.execution_attempts a WHERE a.tenant_id=NEW.tenant_id AND a.outbound_id=NEW.id) THEN
   RAISE EXCEPTION 'prepared execution link cannot be omitted' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO item FROM public.execution_orders WHERE tenant_id=NEW.tenant_id AND id=NEW.execution_id;
 SELECT * INTO attempt FROM public.execution_attempts WHERE tenant_id=NEW.tenant_id
  AND id=NEW.execution_attempt_id AND order_id=NEW.execution_id FOR UPDATE;
 IF NOT FOUND OR (NEW.account_id,NEW.provider,NEW.product_line,NEW.target_native_id)
  IS DISTINCT FROM (item.account_id,item.provider,item.product_line,item.target_native_id) THEN
  RAISE EXCEPTION 'execution request context mismatch' USING ERRCODE='23514'; END IF;
 -- 独立只读允许晚到/隔离后留档，不给再次写入资格。
 IF NEW.effect='READ' THEN RETURN NEW; END IF;
 IF NEW.effect<>'WRITE' OR NEW.id<>attempt.outbound_id OR NEW.user_id<>item.created_by
  OR NEW.binding_id<>attempt.binding_id OR NEW.authorization_id<>attempt.authorization_id
  OR NEW.capability_version<>attempt.capability_version OR NEW.operation_key<>attempt.operation_key
  OR NEW.membership_id<>attempt.membership_id THEN
  RAISE EXCEPTION 'execution write context mismatch' USING ERRCODE='23514'; END IF;
 PERFORM access.assert_execution_wire(NEW.membership_id,NEW.user_id,item.id,attempt.id);
 given:=current_setting('app.execution_dispatch_secret',true);
 IF current_setting('app.execution_wire_digest',true) IS DISTINCT FROM attempt.wire_digest
  OR current_setting('app.execution_schema_digest',true) IS DISTINCT FROM attempt.schema_digest THEN
  RAISE EXCEPTION 'actual wire differs from frozen request' USING ERRCODE='23514'; END IF;
 IF given IS NULL OR attempt.dispatch_digest<>encode(sha256(convert_to(given,'UTF8')),'hex')
  OR attempt.dispatch_consumed_at IS NOT NULL THEN
  RAISE EXCEPTION 'one-use dispatch admission required' USING ERRCODE='42501'; END IF;
 UPDATE public.execution_attempts SET dispatch_consumed_at=clock_timestamp()
  WHERE tenant_id=NEW.tenant_id AND id=attempt.id AND dispatch_consumed_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'dispatch already consumed' USING ERRCODE='40001'; END IF;
 RETURN NEW;
END $$;

-- 统一在途定性：共享读模型与预占强校验使用同一原请求证据。
CREATE FUNCTION access.platform_write_resolved(p_tenant bigint,p_outbound uuid) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.platform_requests root WHERE root.tenant_id=p_tenant AND root.id=p_outbound
  AND root.effect='WRITE' AND (
   EXISTS(SELECT 1 FROM public.verified_action_facts f WHERE f.tenant_id=p_tenant AND f.write_outbound_id=root.id)
   OR EXISTS(SELECT 1 FROM public.platform_raw_payloads raw WHERE raw.tenant_id=p_tenant AND raw.outbound_id=root.id
    AND raw.phase IN ('RESPONSE','ERROR') AND raw.external_outcome IN ('REJECTED','NOT_SENT'))
   OR EXISTS(SELECT 1 FROM public.budget_job_finalizations done JOIN public.execution_recovery_evidence proof
    ON (proof.tenant_id,proof.id)=(done.tenant_id,done.recovery_proof_id)
    WHERE done.tenant_id=p_tenant AND done.kind='RELEASE' AND done.proof_kind='RECOVERY_NOT_SENT'
     AND proof.kind='NOT_SENT' AND proof.evidence_level='E1' AND proof.outbound_id=root.id
     AND proof.order_id=root.execution_id AND proof.attempt_id=root.execution_attempt_id)));
$$;
REVOKE ALL ON FUNCTION access.platform_write_resolved(bigint,uuid) FROM PUBLIC,app_login,app_auth;

CREATE OR REPLACE FUNCTION access.budget_hold(p_member uuid,p_user uuid,p_job uuid,p_version bigint,
 p_key text,p_assessment uuid,p_request uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); job public.command_jobs; hold public.job_budget_holds;
 policy public.risk_policies; risk public.risk_assessments; ledger public.budget_ledgers;
 stamp timestamptz; delta bigint; old_value bigint; new_value bigint; held_sum numeric; count_buckets integer:=0;
 identity uuid; reservations jsonb:='[]';
BEGIN
 PERFORM access.job_state_context(p_member,p_user,p_job,'CONFIRM');
 IF p_key IS NULL OR p_key !~ '^[A-Za-z0-9_.:-]{1,80}$' OR p_request IS NULL OR p_version<1 THEN
  RAISE EXCEPTION 'invalid hold input' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job FOR UPDATE;
 SELECT * INTO hold FROM public.job_budget_holds WHERE tenant_id=tenant AND (job_id=p_job OR idempotency_key=p_key);
 IF FOUND THEN
  IF hold.job_id<>p_job OR hold.idempotency_key<>p_key OR hold.intent_hash<>job.intent_hash
   OR hold.job_version<>p_version THEN
   RAISE EXCEPTION 'hold key mismatch' USING ERRCODE='23505'; END IF;
  RETURN jsonb_build_object('holdId',hold.id,'state',hold.state,'replayed',true);
 END IF;
 stamp:=clock_timestamp();
 IF job.version<>p_version OR job.status<>'DRAFT' OR job.expires_at<=stamp
  OR job.claimed_at IS NOT NULL OR job.dispatch_started_at IS NOT NULL THEN
  RAISE EXCEPTION 'job no longer confirmable' USING ERRCODE='40001'; END IF;
 SELECT * INTO policy FROM public.risk_policies WHERE tenant_id=tenant;
 IF NOT FOUND OR (job.document#>>'{risk_summary,policy_version}')::bigint<>policy.version THEN
  RAISE EXCEPTION 'policy changed' USING ERRCODE='P0210'; END IF;
 SELECT * INTO risk FROM public.risk_assessments WHERE tenant_id=tenant AND id=p_assessment
  AND job_id=p_job AND user_id=p_user AND membership_id=p_member AND job_version=p_version
  AND stage='CONFIRM' AND policy_id=policy.id AND policy_version=policy.version
  AND checked_at<=stamp AND expires_at>stamp AND summary->>'result'='REVIEW_REQUIRED'
  AND summary->>'source'='SERVER' AND jsonb_array_length(summary->'checks')>0
  AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(summary->'checks') c WHERE c->>'passed' IS DISTINCT FROM 'true');
 IF NOT FOUND THEN RAISE EXCEPTION 'fresh risk admission required' USING ERRCODE='22023'; END IF;
 IF job.document->>'action_type'<>'CHANGE_DAILY_BUDGET' THEN
  RETURN jsonb_build_object('state','NOT_REQUIRED','replayed',false); END IF;
 IF job.document#>>'{old_value,kind}'<>'MONEY' OR job.document#>>'{new_value,kind}'<>'MONEY'
  OR job.document#>>'{old_value,currency}'<>'CNY' OR job.document#>>'{new_value,currency}'<>'CNY' THEN
  RAISE EXCEPTION 'CNY budget required' USING ERRCODE='22023'; END IF;
 old_value:=(job.document#>>'{old_value,amount_minor}')::bigint;
 new_value:=(job.document#>>'{new_value,amount_minor}')::bigint; delta:=greatest(new_value-old_value,0);
 IF delta=0 THEN RETURN jsonb_build_object('state','NOT_REQUIRED','replayed',false); END IF;
 IF EXISTS(SELECT 1 FROM public.job_budget_holds h WHERE h.tenant_id=tenant AND h.account_id=job.account_id
  AND h.campaign_id=job.campaign_id AND h.state='HELD') OR EXISTS(
   SELECT 1 FROM public.platform_requests r WHERE r.tenant_id=tenant AND r.account_id=job.account_id AND r.effect='WRITE'
    AND NOT access.platform_write_resolved(tenant,r.id)) THEN
  RAISE EXCEPTION 'conflicting or unknown write' USING ERRCODE='P0211'; END IF;
 IF (SELECT count(*) FROM public.budget_ledgers WHERE tenant_id=tenant AND policy_id=policy.id
  AND bucket_key='TENANT_DAILY' AND currency='CNY' AND account_id IS NULL)<>1 THEN
  RAISE EXCEPTION 'root budget unknown' USING ERRCODE='22023'; END IF;
 identity:=gen_random_uuid();
 INSERT INTO public.job_budget_holds(id,tenant_id,job_id,account_id,campaign_id,policy_id,policy_version,
  job_version,intent_hash,idempotency_key,state,request_id,assessment_id)
 VALUES(identity,tenant,p_job,job.account_id,job.campaign_id,policy.id,policy.version,p_version,
  job.intent_hash,p_key,'HELD',p_request,p_assessment);
 FOR ledger IN SELECT * FROM public.budget_ledgers WHERE tenant_id=tenant AND policy_id=policy.id
  AND (account_id IS NULL OR account_id=job.account_id) ORDER BY id FOR UPDATE LOOP
  count_buckets:=count_buckets+1;
  SELECT coalesce(sum(amount_minor),0) INTO held_sum FROM public.budget_reservations
   WHERE tenant_id=tenant AND ledger_id=ledger.id AND state='HELD';
  IF ledger.policy_version<>policy.version OR ledger.currency<>'CNY' OR ledger.data_state<>'CONFIRMED'
   OR ledger.period_start<>(stamp AT TIME ZONE 'Asia/Shanghai')::date OR ledger.confirmed_total_minor IS NULL
   OR ledger.fetched_at>stamp OR ledger.valid_until<=stamp OR ledger.freeze_reason IS NOT NULL
   OR ledger.coverage_kind<>'ALL_CONFIGURED_INCLUDING_PAUSED' OR NOT job.account_id=ANY(ledger.covered_account_ids)
   OR ledger.confirmed_total_minor<old_value OR ledger.reserved_increase_minor<>held_sum
   OR NOT access.budget_basis_current(ledger.id) THEN
   RAISE EXCEPTION 'budget basis unknown or frozen' USING ERRCODE='22023'; END IF;
  IF ledger.confirmed_total_minor::numeric+ledger.reserved_increase_minor+delta>ledger.limit_minor THEN
   RAISE EXCEPTION 'budget capacity exceeded' USING ERRCODE='P0212'; END IF;
  UPDATE public.budget_ledgers SET reserved_increase_minor=reserved_increase_minor+delta,version=version+1
   WHERE tenant_id=tenant AND id=ledger.id;
  INSERT INTO public.budget_reservations(id,tenant_id,hold_id,job_id,ledger_id,amount_minor,state)
   VALUES(gen_random_uuid(),tenant,identity,p_job,ledger.id,delta,'HELD');
  reservations:=reservations||jsonb_build_array(jsonb_build_object('ledgerId',ledger.id,'amountMinor',delta));
 END LOOP;
 IF count_buckets=0 THEN RAISE EXCEPTION 'no budget bucket' USING ERRCODE='22023'; END IF;
 RETURN jsonb_build_object('holdId',identity,'state','HELD','replayed',false);
END $$;
REVOKE ALL ON FUNCTION access.budget_hold(uuid,uuid,uuid,bigint,text,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_hold(uuid,uuid,uuid,bigint,text,uuid,uuid) TO app_login;


CREATE OR REPLACE FUNCTION access.risk_action_summary(p_member uuid,p_user uuid,p_job uuid,p_shop uuid,
 p_account uuid,p_target uuid,p_stage text,p_at timestamptz) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); permission text; since_at timestamptz;
 count_today bigint; last_at timestamptz; unresolved bigint;
BEGIN
 permission:=CASE WHEN p_stage='APPROVE' THEN 'task.approve' ELSE 'task.create' END;
 IF p_stage NOT IN ('CREATE','CONFIRM','APPROVE','EXECUTE') OR p_at IS NULL OR p_at>clock_timestamp()
  OR p_user IS DISTINCT FROM public.current_actor_id() OR NOT EXISTS(
  SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  WHERE m.tenant_id=tenant AND m.id=p_member AND m.user_id=p_user AND m.status='ACTIVE'
   AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM public.tenants t WHERE t.id=tenant AND t.status='ACTIVE' AND t.deleted_at IS NULL)
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id)
    WHERE g.tenant_id=tenant AND g.membership_id=p_member AND rp.permission_code=permission)
   AND EXISTS(SELECT 1 FROM public.shops s JOIN public.data_scopes ds ON ds.tenant_id=s.tenant_id
    WHERE s.tenant_id=tenant AND s.id=p_shop AND s.deleted_at IS NULL AND ds.membership_id=p_member
     AND (ds.permissions_mask IS NULL OR permission=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.shop_id=s.id OR ds.entity_id=s.entity_id))
   AND EXISTS(SELECT 1 FROM public.ad_accounts a JOIN public.data_scopes ds ON ds.tenant_id=a.tenant_id
    WHERE a.tenant_id=tenant AND a.id=p_account AND a.deleted_at IS NULL AND ds.membership_id=p_member
     AND (ds.permissions_mask IS NULL OR permission=ANY(ds.permissions_mask))
     AND (ds.scope_type='ALL_TENANT' OR ds.account_id=a.id OR ds.entity_id=a.entity_id))
 ) OR NOT EXISTS(SELECT 1 FROM public.campaigns c WHERE c.tenant_id=tenant AND c.id=p_target
  AND c.shop_id=p_shop AND c.account_id=p_account) THEN
  RAISE EXCEPTION 'risk window unavailable' USING ERRCODE='42501'; END IF;
 IF p_stage<>'CREATE' THEN
  PERFORM access.job_state_context(p_member,p_user,p_job,
   CASE p_stage WHEN 'APPROVE' THEN 'APPROVE' WHEN 'EXECUTE' THEN 'CLAIM' ELSE 'CONFIRM' END);
  IF NOT EXISTS(SELECT 1 FROM public.command_jobs j WHERE j.tenant_id=tenant AND j.id=p_job
   AND j.account_id=p_account AND j.shop_id=p_shop AND j.target_id=p_target) THEN
   RAISE EXCEPTION 'risk window context mismatch' USING ERRCODE='42501'; END IF;
 END IF;
 since_at:=date_trunc('day',p_at AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai';
 SELECT count(*) INTO count_today FROM public.verified_action_facts
  WHERE tenant_id=tenant AND account_id=p_account AND verified_at>=since_at
   AND verified_at<since_at+interval '1 day';
 SELECT max(verified_at) INTO last_at FROM public.verified_action_facts
  WHERE tenant_id=tenant AND campaign_id=p_target;
 SELECT count(*) INTO unresolved FROM public.platform_requests r
  WHERE r.tenant_id=tenant AND r.account_id=p_account AND r.effect='WRITE'
   AND NOT access.platform_write_resolved(tenant,r.id);
 RETURN jsonb_build_object('verifiedToday',count_today,'lastVerifiedAt',last_at,
   'unresolvedWrites',unresolved,'dayStart',since_at);
END $$;
REVOKE ALL ON FUNCTION access.risk_action_summary(uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.risk_action_summary(uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz) TO app_login;

