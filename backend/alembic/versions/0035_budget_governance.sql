CREATE TABLE budget_discrepancies (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,ledger_id uuid NOT NULL,observation_id uuid,
 coverage_digest varchar(64) NOT NULL,account_id uuid NOT NULL,native_id varchar(128) NOT NULL,
 before_minor bigint,observed_minor bigint NOT NULL CHECK(observed_minor>=0),
 observed_at timestamptz NOT NULL,outbound_id uuid NOT NULL,related_job_id uuid,
 classification varchar(30) NOT NULL CHECK(classification IN (
  'EXTERNAL_CHANGE','OWN_PENDING','INFLIGHT_MISMATCH','STALE_SOURCE')),
 state varchar(8) NOT NULL CHECK(state IN ('OPEN','RESOLVED')),
 requires_reconciliation boolean NOT NULL,resolved_by_finalization uuid,
 request_id uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),resolved_at timestamptz,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,ledger_id,coverage_digest,account_id,native_id),
 FOREIGN KEY(tenant_id,ledger_id) REFERENCES budget_ledgers(tenant_id,id),
 FOREIGN KEY(tenant_id,observation_id) REFERENCES budget_observations(tenant_id,id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
 FOREIGN KEY(tenant_id,outbound_id,account_id) REFERENCES platform_requests(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,related_job_id) REFERENCES command_jobs(tenant_id,id),
 FOREIGN KEY(tenant_id,resolved_by_finalization) REFERENCES budget_job_finalizations(tenant_id,id),
 CHECK((state='RESOLVED')=(resolved_at IS NOT NULL)),
 CHECK(state<>'OPEN' OR requires_reconciliation)
);
CREATE TABLE budget_alerts (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,ledger_id uuid NOT NULL,
 kind varchar(20) NOT NULL CHECK(kind IN ('OVER_LIMIT','RECONCILIATION','STALE_SOURCE')),
 state varchar(8) NOT NULL CHECK(state IN ('OPEN','RESOLVED')),version bigint NOT NULL DEFAULT 1,
 first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 resolved_at timestamptz,observation_id uuid,request_id uuid NOT NULL,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,ledger_id,kind),
 FOREIGN KEY(tenant_id,ledger_id) REFERENCES budget_ledgers(tenant_id,id),
 FOREIGN KEY(tenant_id,observation_id) REFERENCES budget_observations(tenant_id,id),
 CHECK((state='RESOLVED')=(resolved_at IS NOT NULL))
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['budget_discrepancies','budget_alerts'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
 END LOOP;
END $$;
-- 私有在途申请ID不通过租户级SELECT传播；经理经精确policy.edit/ALL_TENANT门读取受控摘要。
REVOKE ALL ON budget_discrepancies,budget_alerts FROM PUBLIC,app_login,app_auth;

CREATE FUNCTION access.budget_governance_scope(p_member uuid,p_user uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id();
BEGIN
 IF p_user IS DISTINCT FROM public.current_actor_id() OR NOT EXISTS(
  SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.tenants t ON t.id=m.tenant_id WHERE m.tenant_id=tenant AND m.id=p_member AND m.user_id=p_user
   AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL AND t.status='ACTIVE' AND t.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM public.role_grants g JOIN public.role_permissions rp
    ON (rp.tenant_id,rp.role_id)=(g.tenant_id,g.role_id) WHERE g.tenant_id=tenant AND g.membership_id=m.id
     AND rp.permission_code='policy.edit')
   AND EXISTS(SELECT 1 FROM public.data_scopes ds WHERE ds.tenant_id=tenant AND ds.membership_id=m.id
    AND ds.scope_type='ALL_TENANT' AND (ds.permissions_mask IS NULL OR 'policy.edit'=ANY(ds.permissions_mask)))) THEN
  RAISE EXCEPTION 'budget governance scope denied' USING ERRCODE='42501'; END IF;
END $$;
REVOKE ALL ON FUNCTION access.budget_governance_scope(uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_governance_scope(uuid,uuid) TO app_login;

CREATE FUNCTION access.budget_refresh_alerts(p_ledger uuid,p_observation uuid,p_request uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); ledger public.budget_ledgers; alert_kind text; active boolean;
BEGIN
 SELECT * INTO ledger FROM public.budget_ledgers WHERE tenant_id=tenant AND id=p_ledger;
 FOREACH alert_kind IN ARRAY ARRAY['OVER_LIMIT','RECONCILIATION','STALE_SOURCE'] LOOP
  active:=CASE alert_kind WHEN 'OVER_LIMIT' THEN ledger.confirmed_total_minor+ledger.reserved_increase_minor>ledger.limit_minor
   WHEN 'STALE_SOURCE' THEN EXISTS(SELECT 1 FROM public.budget_discrepancies WHERE tenant_id=tenant
    AND ledger_id=p_ledger AND state='OPEN' AND classification='STALE_SOURCE')
   ELSE EXISTS(SELECT 1 FROM public.budget_discrepancies WHERE tenant_id=tenant AND ledger_id=p_ledger AND state='OPEN') END;
  IF active THEN
   INSERT INTO public.budget_alerts(id,tenant_id,ledger_id,kind,state,observation_id,request_id)
   VALUES(gen_random_uuid(),tenant,p_ledger,alert_kind,'OPEN',p_observation,p_request)
   ON CONFLICT(tenant_id,ledger_id,kind) DO UPDATE SET state='OPEN',resolved_at=NULL,
    last_seen_at=clock_timestamp(),version=budget_alerts.version+1,observation_id=p_observation,request_id=p_request
    WHERE budget_alerts.state='RESOLVED' OR budget_alerts.observation_id IS DISTINCT FROM p_observation;
  ELSE
   UPDATE public.budget_alerts SET state='RESOLVED',resolved_at=clock_timestamp(),last_seen_at=clock_timestamp(),
    version=version+1,request_id=p_request WHERE tenant_id=tenant AND ledger_id=p_ledger AND budget_alerts.kind=alert_kind AND state='OPEN';
  END IF;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION access.budget_refresh_alerts(uuid,uuid,uuid) FROM PUBLIC,app_login,app_auth;

CREATE FUNCTION access.budget_component_clock(p_tenant bigint,p_outbound uuid) RETURNS timestamptz
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT CASE f.clock_kind WHEN 'SOURCE' THEN p.source_updated_at ELSE p.fetched_at END
 FROM public.sync_pages p JOIN public.sync_runs r ON (r.tenant_id,r.id)=(p.tenant_id,p.run_id)
 JOIN public.resource_freshness_policies f ON (f.tenant_id,f.source_id)=(r.tenant_id,r.source_id)
 WHERE p.tenant_id=p_tenant AND p.outbound_id=p_outbound
$$;
REVOKE ALL ON FUNCTION access.budget_component_clock(bigint,uuid) FROM PUBLIC,app_login,app_auth;

ALTER FUNCTION access.budget_initialize(uuid,uuid,uuid,bigint) RENAME TO budget_initialize_snapshot;
REVOKE EXECUTE ON FUNCTION access.budget_initialize_snapshot(uuid,uuid,uuid,bigint) FROM app_login;
CREATE FUNCTION access.budget_initialize(p_member uuid,p_user uuid,p_request uuid,p_expected_policy bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); before_ledger public.budget_ledgers;
 old_observation public.budget_observations; observation public.budget_observations;
 ledger public.budget_ledgers; value jsonb; component jsonb; old_component jsonb; items jsonb;
 actual bigint; previous record; pending record; verified record; incoming_clock timestamptz;
 classification text; needs boolean; stale boolean:=false; related uuid; missing_outbound uuid;
 digest text;
BEGIN
 PERFORM access.budget_governance_scope(p_member,p_user);
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT * INTO before_ledger FROM public.budget_ledgers WHERE tenant_id=tenant AND bucket_key='TENANT_DAILY' AND currency='CNY';
 SELECT * INTO old_observation FROM public.budget_observations WHERE tenant_id=tenant AND ledger_id=before_ledger.id
  ORDER BY recorded_at DESC,id DESC LIMIT 1;
 BEGIN
  value:=access.budget_initialize_snapshot(p_member,p_user,p_request,p_expected_policy);
  SELECT * INTO observation FROM public.budget_observations WHERE tenant_id=tenant AND id=(value->>'observationId')::uuid;
  items:=observation.components;
  IF (value->>'replayed')::boolean IS DISTINCT FROM true THEN
   FOR old_component IN SELECT * FROM jsonb_array_elements(old_observation.components) prior
    WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(observation.components) current
     WHERE current->>'accountId'=prior->>'accountId' AND current->>'nativeId'=prior->>'nativeId') LOOP
    SELECT p.outbound_id INTO missing_outbound FROM public.sync_pages p JOIN public.sync_runs r
     ON (r.tenant_id,r.id)=(p.tenant_id,p.run_id) JOIN public.budget_account_contracts c
     ON (c.tenant_id,c.source_id)=(r.tenant_id,r.source_id)
     WHERE p.tenant_id=tenant AND c.account_id=(old_component->>'accountId')::uuid AND p.terminal
      AND EXISTS(SELECT 1 FROM jsonb_array_elements(observation.sources) source WHERE source->>'runId'=r.id::text)
     ORDER BY r.source_version DESC LIMIT 1;
    IF missing_outbound IS NOT NULL THEN items:=items||jsonb_build_array(old_component||
     jsonb_build_object('budgetMinor',0,'outboundId',missing_outbound)); END IF;
   END LOOP;
   FOR component IN SELECT * FROM jsonb_array_elements(items) LOOP
    incoming_clock:=access.budget_component_clock(tenant,(component->>'outboundId')::uuid);
    SELECT a.component_after,f.job_id,
     coalesce((raw.payload#>>'{data,data,source_updated_at}')::timestamptz,raw.created_at) AS source_at
     INTO verified FROM public.budget_adjustments a JOIN public.budget_job_finalizations done
     ON (done.tenant_id,done.id)=(a.tenant_id,a.finalization_id)
     JOIN public.verified_action_facts f ON f.id=done.verified_fact_id
     JOIN public.campaigns c ON (c.tenant_id,c.id)=(a.tenant_id,a.campaign_id)
     JOIN public.platform_raw_payloads raw ON (raw.tenant_id,raw.id)=(f.tenant_id,f.read_payload_id)
     WHERE a.tenant_id=tenant AND a.account_id=(component->>'accountId')::uuid AND c.native_id=component->>'nativeId'
      AND done.kind='MATCH' ORDER BY a.created_at DESC,a.id DESC LIMIT 1;
    IF FOUND AND verified.component_after<>(component->>'budgetMinor')::bigint
      AND incoming_clock<verified.source_at THEN
     RAISE EXCEPTION 'older source cannot replace verified B' USING ERRCODE='P0214';
    END IF;
   END LOOP;
  END IF;
 EXCEPTION WHEN SQLSTATE 'P0214' THEN
  -- 子事务回滚新观察对B的替换；原始同步/平台档案已在此前独立提交，保留实际旧来源证明。
  stale:=true;
 END;
 IF stale THEN
  INSERT INTO public.budget_discrepancies(id,tenant_id,ledger_id,observation_id,coverage_digest,account_id,
   native_id,before_minor,observed_minor,observed_at,outbound_id,related_job_id,classification,state,
   requires_reconciliation,request_id)
  VALUES(gen_random_uuid(),tenant,before_ledger.id,old_observation.id,observation.coverage_digest,
   (component->>'accountId')::uuid,component->>'nativeId',verified.component_after,
   (component->>'budgetMinor')::bigint,incoming_clock,(component->>'outboundId')::uuid,
   verified.job_id,'STALE_SOURCE','OPEN',true,p_request) ON CONFLICT DO NOTHING;
  value:=jsonb_build_object('ledgerId',before_ledger.id,'observationId',old_observation.id,'replayed',false);
 ELSE
  IF (value->>'replayed')::boolean IS DISTINCT FROM true AND old_observation.id IS NOT NULL THEN
   -- 已消失配置的0组件也使用新批次终页，且在上面的来源顺序校验范围内。
   FOR component IN SELECT * FROM jsonb_array_elements(items) LOOP
    SELECT prior INTO old_component FROM jsonb_array_elements(old_observation.components) prior
     WHERE prior->>'accountId'=component->>'accountId' AND prior->>'nativeId'=component->>'nativeId';
    actual:=(old_component->>'budgetMinor')::bigint;
    SELECT a.component_after INTO previous FROM public.budget_adjustments a JOIN public.campaigns c
     ON (c.tenant_id,c.id)=(a.tenant_id,a.campaign_id) WHERE a.tenant_id=tenant AND a.ledger_id=before_ledger.id
      AND a.observation_id=old_observation.id AND a.account_id=(component->>'accountId')::uuid
      AND c.native_id=component->>'nativeId' AND a.component_after IS NOT NULL ORDER BY a.created_at DESC,a.id DESC LIMIT 1;
    IF FOUND THEN actual:=previous.component_after; END IF;
    SELECT j.id,j.document->'new_value' AS requested,raw.created_at AS accepted_at INTO pending
     FROM public.execution_orders o JOIN public.command_jobs j ON (j.tenant_id,j.id)=(o.tenant_id,o.command_job_id)
     LEFT JOIN public.execution_attempts a ON (a.tenant_id,a.order_id,a.attempt_no)=(o.tenant_id,o.id,o.current_attempt)
     LEFT JOIN public.platform_raw_payloads raw ON raw.tenant_id=a.tenant_id AND raw.outbound_id=a.outbound_id
      AND raw.phase='RESPONSE' AND raw.external_outcome='ACCEPTED'
     WHERE o.tenant_id=tenant AND o.account_id=(component->>'accountId')::uuid AND o.target_native_id=component->>'nativeId'
      AND o.action_type='CHANGE_DAILY_BUDGET' AND NOT EXISTS(SELECT 1 FROM public.budget_job_finalizations done
       WHERE done.tenant_id=tenant AND done.job_id=j.id) ORDER BY o.created_at DESC LIMIT 1;
    related:=pending.id;
    incoming_clock:=access.budget_component_clock(tenant,(component->>'outboundId')::uuid);
    needs:=pending.id IS NOT NULL AND pending.accepted_at IS NOT NULL AND incoming_clock>=pending.accepted_at
     AND (component->>'budgetMinor')::bigint IS DISTINCT FROM (pending.requested->>'amount_minor')::bigint;
    IF actual IS NOT DISTINCT FROM (component->>'budgetMinor')::bigint AND NOT needs THEN CONTINUE; END IF;
    classification:=CASE WHEN needs THEN 'INFLIGHT_MISMATCH' WHEN pending.id IS NOT NULL
     AND (component->>'budgetMinor')::bigint=(pending.requested->>'amount_minor')::bigint
     AND incoming_clock>=pending.accepted_at THEN 'OWN_PENDING' ELSE 'EXTERNAL_CHANGE' END;
    INSERT INTO public.budget_discrepancies(id,tenant_id,ledger_id,observation_id,coverage_digest,account_id,
     native_id,before_minor,observed_minor,observed_at,outbound_id,related_job_id,classification,state,
     requires_reconciliation,request_id,resolved_at)
    VALUES(gen_random_uuid(),tenant,(value->>'ledgerId')::uuid,observation.id,observation.coverage_digest,
     (component->>'accountId')::uuid,component->>'nativeId',actual,(component->>'budgetMinor')::bigint,
     incoming_clock,(component->>'outboundId')::uuid,related,classification,
     CASE WHEN needs THEN 'OPEN' ELSE 'RESOLVED' END,needs,p_request,CASE WHEN NOT needs THEN clock_timestamp() END)
    ON CONFLICT DO NOTHING;
   END LOOP;
  END IF;
 END IF;
 SELECT EXISTS(SELECT 1 FROM public.budget_discrepancies WHERE tenant_id=tenant
  AND ledger_id=(value->>'ledgerId')::uuid AND state='OPEN') INTO needs;
 IF needs THEN UPDATE public.budget_ledgers SET freeze_reason=CASE
  WHEN freeze_reason IS NOT NULL AND freeze_reason NOT IN ('OVER_LIMIT','RECONCILIATION') THEN freeze_reason
  ELSE 'RECONCILIATION' END,version=version+1 WHERE tenant_id=tenant AND id=(value->>'ledgerId')::uuid; END IF;
 PERFORM access.budget_refresh_alerts((value->>'ledgerId')::uuid,(value->>'observationId')::uuid,p_request);
 SELECT encode(sha256(convert_to(coalesce(jsonb_agg(jsonb_build_object('id',id,'state',state)
  ORDER BY id)::text,'[]'),'UTF8')),'hex') INTO digest FROM public.budget_discrepancies
  WHERE tenant_id=tenant AND ledger_id=(value->>'ledgerId')::uuid;
 RETURN value||jsonb_build_object('governed',true,'snapshotApplied',NOT stale,'needsReconciliation',needs,'governanceDigest',digest);
END $$;
REVOKE ALL ON FUNCTION access.budget_initialize(uuid,uuid,uuid,bigint) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_initialize(uuid,uuid,uuid,bigint) TO app_login;

CREATE FUNCTION access.budget_resolve_discrepancy(p_member uuid,p_user uuid,p_id uuid,p_finalization uuid,p_request uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); item public.budget_discrepancies;
 finished public.budget_job_finalizations; fact public.verified_action_facts;
 ledger public.budget_ledgers; receipt public.execution_receipts; stamp timestamptz;
 held_sum numeric; frozen text; basis uuid; result jsonb;
BEGIN
 PERFORM access.budget_governance_scope(p_member,p_user);
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT * INTO item FROM public.budget_discrepancies WHERE tenant_id=tenant AND id=p_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'budget discrepancy unavailable' USING ERRCODE='P0002'; END IF;
 IF item.state='RESOLVED' THEN
  IF item.resolved_by_finalization IS DISTINCT FROM p_finalization THEN
   RAISE EXCEPTION 'resolution proof cannot change' USING ERRCODE='23505'; END IF;
  RETURN jsonb_build_object('discrepancyId',p_id,'resolved',true,'replayed',true,'finalizationId',p_finalization);
 END IF;
 SELECT * INTO finished FROM public.budget_job_finalizations WHERE tenant_id=tenant AND id=p_finalization
  AND job_id=item.related_job_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'matching finalization evidence required' USING ERRCODE='22023'; END IF;
 IF finished.kind='MATCH' AND finished.proof_kind='VERIFIED_MATCH' THEN
  SELECT * INTO fact FROM public.verified_action_facts WHERE tenant_id=tenant AND id=finished.verified_fact_id;
  SELECT coalesce((raw.payload#>>'{data,data,source_updated_at}')::timestamptz,raw.created_at) INTO stamp
   FROM public.platform_raw_payloads raw WHERE raw.tenant_id=tenant AND raw.id=fact.read_payload_id;
 ELSIF finished.kind='RELEASE' AND finished.proof_kind='PLATFORM_REJECTION' THEN
  SELECT * INTO receipt FROM public.execution_receipts WHERE tenant_id=tenant AND id=finished.receipt_id
   AND applied AND source='PLATFORM' AND accepted=false AND evidence_level='E1';
  IF NOT FOUND THEN RAISE EXCEPTION 'current E1 proof required' USING ERRCODE='22023'; END IF;
  SELECT created_at INTO stamp FROM public.platform_raw_payloads WHERE tenant_id=tenant AND id=receipt.raw_id;
 ELSE RAISE EXCEPTION 'platform resolution proof required' USING ERRCODE='22023';
 END IF;
 IF stamp IS NULL OR stamp<item.observed_at THEN
  RAISE EXCEPTION 'resolution older than discrepancy' USING ERRCODE='22023'; END IF;
 SELECT * INTO ledger FROM public.budget_ledgers WHERE tenant_id=tenant AND id=item.ledger_id FOR UPDATE;
 SELECT coalesce(sum(amount_minor),0) INTO held_sum FROM public.budget_reservations
  WHERE tenant_id=tenant AND ledger_id=ledger.id AND state='HELD';
 IF NOT access.budget_basis_current(ledger.id) OR held_sum<>ledger.reserved_increase_minor
  OR ledger.policy_version IS DISTINCT FROM (SELECT version FROM public.risk_policies WHERE tenant_id=tenant) THEN
  RAISE EXCEPTION 'current complete budget basis required' USING ERRCODE='22023'; END IF;
 UPDATE public.budget_discrepancies SET state='RESOLVED',resolved_at=clock_timestamp(),resolved_by_finalization=p_finalization
  WHERE tenant_id=tenant AND id=p_id;
 frozen:=ledger.freeze_reason;
 IF frozen='RECONCILIATION' AND NOT EXISTS(SELECT 1 FROM public.budget_discrepancies
  WHERE tenant_id=tenant AND ledger_id=ledger.id AND state='OPEN') THEN
  frozen:=CASE WHEN ledger.confirmed_total_minor+ledger.reserved_increase_minor>ledger.limit_minor THEN 'OVER_LIMIT' END;
  UPDATE public.budget_ledgers SET freeze_reason=frozen,version=version+1 WHERE tenant_id=tenant AND id=ledger.id;
 END IF;
 SELECT id INTO basis FROM public.budget_observations WHERE tenant_id=tenant AND ledger_id=ledger.id
  AND 'budget-observation/'||coverage_digest=ledger.source_ref;
 PERFORM access.budget_refresh_alerts(ledger.id,basis,p_request);
 result:=jsonb_build_object('discrepancyId',p_id,'resolved',true,'replayed',false,
  'finalizationId',p_finalization,'freezeReason',frozen);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION access.budget_resolve_discrepancy(uuid,uuid,uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_resolve_discrepancy(uuid,uuid,uuid,uuid,uuid) TO app_login;

CREATE FUNCTION access.budget_governance_summary(p_member uuid,p_user uuid,p_ledger uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); ledger public.budget_ledgers; result jsonb;
BEGIN
 PERFORM access.budget_governance_scope(p_member,p_user);
 SELECT * INTO ledger FROM public.budget_ledgers WHERE tenant_id=tenant AND id=p_ledger;
 IF NOT FOUND THEN RAISE EXCEPTION 'budget ledger unavailable' USING ERRCODE='P0002'; END IF;
 SELECT jsonb_build_object('ledgerId',ledger.id,'B',ledger.confirmed_total_minor,'R',ledger.reserved_increase_minor,
  'limitMinor',ledger.limit_minor,'freezeReason',ledger.freeze_reason,'version',ledger.version,
  'discrepancies',coalesce((SELECT jsonb_agg(entry ORDER BY priority,created_at DESC) FROM(
   SELECT jsonb_build_object('id',d.id,'accountId',d.account_id,'nativeId',d.native_id,'beforeMinor',d.before_minor,
    'observedMinor',d.observed_minor,'observedAt',d.observed_at,'classification',d.classification,'state',d.state,
    'candidateFinalizationId',done.id,'resolvedByFinalization',d.resolved_by_finalization) AS entry,
    CASE WHEN d.state='OPEN' THEN 0 ELSE 1 END AS priority,d.created_at
   FROM public.budget_discrepancies d LEFT JOIN public.budget_job_finalizations done
    ON (done.tenant_id,done.job_id)=(d.tenant_id,d.related_job_id) WHERE d.tenant_id=tenant AND d.ledger_id=ledger.id
   ORDER BY priority,d.created_at DESC LIMIT 100) bounded),'[]'::jsonb),
  'discrepancyCount',(SELECT count(*) FROM public.budget_discrepancies WHERE tenant_id=tenant AND ledger_id=ledger.id),
  'alerts',coalesce((SELECT jsonb_agg(jsonb_build_object('id',id,'kind',kind,'state',state,'version',version)
   ORDER BY kind) FROM public.budget_alerts WHERE tenant_id=tenant AND ledger_id=ledger.id),'[]'::jsonb)) INTO result;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION access.budget_governance_summary(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_governance_summary(uuid,uuid,uuid) TO app_login;

INSERT INTO job_transition_rules VALUES('UNSENT_REJECT','APPROVED','FAILED','发送前复核未通过，确认未发送');
CREATE FUNCTION access.budget_reject_unsent(p_member uuid,p_user uuid,p_job uuid,p_version bigint,
 p_assessment uuid,p_document jsonb,p_request uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); job public.command_jobs; risk public.risk_assessments; proof text;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 PERFORM access.job_state_context(p_member,p_user,p_job,'CLAIM');
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job FOR UPDATE;
 IF job.status<>'APPROVED' OR job.version<>p_version OR job.claimed_at IS NOT NULL
  OR job.expires_at<=clock_timestamp()
  OR job.dispatch_started_at IS NOT NULL OR EXISTS(SELECT 1 FROM public.execution_orders
    WHERE tenant_id=tenant AND command_job_id=p_job)
  OR job.document->>'action_type'<>'CHANGE_DAILY_BUDGET'
  OR (job.document#>>'{new_value,amount_minor}')::bigint<=(job.document#>>'{old_value,amount_minor}')::bigint THEN
  RAISE EXCEPTION 'unclaimed positive change required' USING ERRCODE='40001'; END IF;
 SELECT * INTO risk FROM public.risk_assessments WHERE tenant_id=tenant AND id=p_assessment
  AND user_id=p_user AND membership_id=p_member AND job_id=p_job AND job_version=p_version AND stage='EXECUTE'
  AND checked_at<=clock_timestamp() AND expires_at>clock_timestamp() AND summary->>'result'='BLOCKED'
  AND policy_version=(SELECT version FROM public.risk_policies WHERE tenant_id=tenant)
  AND EXISTS(SELECT 1 FROM jsonb_array_elements(summary->'checks') c
   WHERE c->>'code'='AMOUNT_LIMIT' AND c->>'passed'='false');
 IF NOT FOUND OR p_document->'risk_summary' IS DISTINCT FROM risk.summary
  OR p_document->'approval' IS DISTINCT FROM job.document->'approval'
  OR p_document->'execution_receipt' IS DISTINCT FROM 'null'::jsonb
  OR p_document#>>'{error,code}' IS DISTINCT FROM 'RISK_BLOCKED'
  OR p_document#>>'{error,retryable}' IS DISTINCT FROM 'false'
  OR (p_document#>>'{error,at}')::timestamptz IS DISTINCT FROM risk.checked_at
  OR (p_document->>'updated_at')::timestamptz IS DISTINCT FROM risk.checked_at THEN
  RAISE EXCEPTION 'fresh failed budget assessment required' USING ERRCODE='22023'; END IF;
 proof:=encode(sha256(convert_to('unclaimed-risk|'||p_assessment::text,'UTF8')),'hex');
 PERFORM access.job_apply_transition(p_member,p_user,p_job,p_version,'UNSENT_REJECT',p_document,p_request,proof);
END $$;
REVOKE ALL ON FUNCTION access.budget_reject_unsent(uuid,uuid,uuid,bigint,uuid,jsonb,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.budget_reject_unsent(uuid,uuid,uuid,bigint,uuid,jsonb,uuid) TO app_login;

ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK(action IN (
 'profile.update','shop.update','member.status','role.grant','role.revoke','scope.change',
 'project.create','conversation.move','message.send','conversation.attach','task.project',
 'platform.request','connection.oauth.start','connection.oauth.exchange','connection.token.rotate',
 'connection.sync.toggle','connection.disconnect','job.create','job.transition','risk.assess',
 'budget.initialize','budget.reserve','idempotency.start','idempotency.complete','idempotency.recover',
 'execution.prepare','execution.result','budget.finalize','budget.observe','budget.resolve'));
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_object_type_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_object_type_check CHECK(object_type IN (
 'user','shop','member','role','project','conversation','message','job','platform_request',
 'oauth_state','authorization','sync_source','budget_ledger','idempotency_request','execution_order',
 'execution_receipt','budget_finalization','budget_discrepancy'));

-- E1未领取额度拒绝沿用同一释放账，仍须当前UNSENT_REJECT事件与无领取/无执行证明。
CREATE OR REPLACE FUNCTION access.budget_release(p_member uuid,p_user uuid,p_job uuid,p_receipt uuid,p_id uuid,p_request uuid)
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
  IF job.status NOT IN ('REJECTED','CANCELLED','EXPIRED','FAILED') OR job.claimed_at IS NOT NULL
   OR job.dispatch_started_at IS NOT NULL OR EXISTS(SELECT 1 FROM public.execution_orders
    WHERE tenant_id=tenant AND command_job_id=p_job) OR NOT EXISTS(
     SELECT 1 FROM public.job_events e WHERE e.tenant_id=tenant AND e.job_id=p_job AND e.job_version=job.version
      AND e.control_event=CASE job.status WHEN 'REJECTED' THEN 'REJECT' WHEN 'CANCELLED' THEN 'CANCEL' WHEN 'FAILED' THEN 'UNSENT_REJECT' ELSE 'EXPIRE' END) THEN
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

