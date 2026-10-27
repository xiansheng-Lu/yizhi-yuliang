ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition','risk.assess',
 'budget.initialize','budget.reserve'));
CREATE TABLE job_budget_holds (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,job_id uuid NOT NULL,account_id uuid NOT NULL,
 campaign_id uuid NOT NULL,policy_id uuid NOT NULL,policy_version bigint NOT NULL,job_version bigint NOT NULL,
 intent_hash varchar(64) NOT NULL CHECK(intent_hash ~ '^[0-9a-f]{64}$'),
 idempotency_key varchar(80) NOT NULL CHECK(idempotency_key ~ '^[A-Za-z0-9_.:-]{1,80}$'),
 state varchar(8) NOT NULL CHECK(state IN ('HELD','SETTLED','RELEASED')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),request_id uuid NOT NULL,assessment_id uuid NOT NULL,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,job_id),UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,job_id,account_id) REFERENCES command_jobs(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,campaign_id,account_id) REFERENCES campaigns(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,policy_id,policy_version) REFERENCES policy_versions(tenant_id,policy_id,version)
);
CREATE UNIQUE INDEX ux_held_budget_target ON job_budget_holds(tenant_id,account_id,campaign_id) WHERE state='HELD';
CREATE TABLE budget_reservations (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,hold_id uuid NOT NULL,job_id uuid NOT NULL,ledger_id uuid NOT NULL,
 amount_minor bigint NOT NULL CHECK(amount_minor>0),state varchar(8) NOT NULL CHECK(state IN ('HELD','SETTLED','RELEASED')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(tenant_id,id),UNIQUE(tenant_id,job_id,ledger_id),
 FOREIGN KEY(tenant_id,hold_id) REFERENCES job_budget_holds(tenant_id,id),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id),
 FOREIGN KEY(tenant_id,ledger_id) REFERENCES budget_ledgers(tenant_id,id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['job_budget_holds','budget_reservations'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY private_job ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM public.command_jobs j WHERE j.tenant_id=%I.tenant_id AND j.id=%I.job_id))',item,item,item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;

CREATE FUNCTION access.budget_hold(p_member uuid,p_user uuid,p_job uuid,p_version bigint,
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
    AND NOT EXISTS(SELECT 1 FROM public.verified_action_facts f WHERE f.tenant_id=tenant AND f.write_outbound_id=r.id)
    AND NOT EXISTS(SELECT 1 FROM public.platform_raw_payloads raw WHERE raw.tenant_id=tenant AND raw.outbound_id=r.id
     AND raw.phase IN ('RESPONSE','ERROR') AND raw.external_outcome IN ('REJECTED','NOT_SENT'))) THEN
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

CREATE FUNCTION access.budget_own_hold(p_member uuid,p_user uuid,p_job uuid,p_ledger uuid,p_stage text)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); amount bigint;
BEGIN
 IF p_stage='CREATE' THEN RETURN 0; END IF;
 IF p_stage NOT IN ('CONFIRM','APPROVE','EXECUTE') THEN
  RAISE EXCEPTION 'invalid budget stage' USING ERRCODE='22023'; END IF;
 PERFORM access.job_state_context(p_member,p_user,p_job,
  CASE p_stage WHEN 'APPROVE' THEN 'APPROVE' WHEN 'EXECUTE' THEN 'CLAIM' ELSE 'CONFIRM' END);
 SELECT coalesce(sum(r.amount_minor),0) INTO amount FROM public.budget_reservations r
  JOIN public.job_budget_holds h ON (h.tenant_id,h.id)=(r.tenant_id,r.hold_id)
  JOIN public.command_jobs j ON (j.tenant_id,j.id)=(h.tenant_id,h.job_id)
  WHERE r.tenant_id=tenant AND r.job_id=p_job AND r.ledger_id=p_ledger AND r.state='HELD'
   AND h.state='HELD' AND h.intent_hash=j.intent_hash;
 RETURN amount;
END $$;
REVOKE ALL ON FUNCTION access.budget_own_hold(uuid,uuid,uuid,uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_own_hold(uuid,uuid,uuid,uuid,text) TO app_login;
