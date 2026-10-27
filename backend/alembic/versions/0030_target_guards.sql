CREATE TABLE target_execution_guards (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,account_id uuid NOT NULL,provider varchar(20) NOT NULL,
 product_line varchar(30) NOT NULL,target_type varchar(20) NOT NULL CHECK(target_type='AD_PLAN'),
 native_id varchar(128) NOT NULL,fence bigint NOT NULL DEFAULT 0 CHECK(fence>=0),
 state varchar(12) NOT NULL CHECK(state IN ('IDLE','LEASED','QUARANTINED')),
 job_id uuid,lease_owner uuid,lease_until timestamptz,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,account_id,provider,product_line,target_type,native_id),
 FOREIGN KEY(tenant_id,account_id,provider,product_line) REFERENCES ad_accounts(tenant_id,id,provider,product_line),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id),
 CHECK(state='IDLE' OR (job_id IS NOT NULL AND lease_owner IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE TABLE target_guard_claims (
 id uuid PRIMARY KEY,tenant_id bigint NOT NULL,guard_id uuid NOT NULL,job_id uuid NOT NULL,
 fence bigint NOT NULL CHECK(fence>0),lease_owner uuid NOT NULL,lease_until timestamptz NOT NULL,
 assessment_id uuid NOT NULL,request_id uuid NOT NULL,claimed_at timestamptz NOT NULL,
 UNIQUE(tenant_id,guard_id,fence),FOREIGN KEY(tenant_id,guard_id) REFERENCES target_execution_guards(tenant_id,id),
 FOREIGN KEY(tenant_id,job_id) REFERENCES command_jobs(tenant_id,id),
 FOREIGN KEY(assessment_id) REFERENCES risk_assessments(id)
);
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['target_execution_guards','target_guard_claims'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',item);
  EXECUTE format('CREATE POLICY owner_all ON %I TO app_owner USING(true) WITH CHECK(true)',item);
  EXECUTE format('CREATE POLICY private_job ON %I FOR SELECT TO app_login USING(tenant_id=public.current_tenant_id() AND EXISTS(SELECT 1 FROM public.command_jobs j WHERE j.tenant_id=%I.tenant_id AND j.id=%I.job_id))',item,item,item);
  EXECUTE format('GRANT SELECT ON %I TO app_login',item);
 END LOOP;
END $$;

CREATE FUNCTION access.target_claim(p_member uuid,p_user uuid,p_job uuid,p_version bigint,p_fence bigint,
 p_owner uuid,p_seconds integer,p_assessment uuid,p_document jsonb,p_request uuid,p_proof text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); job public.command_jobs; target public.campaigns;
 guard public.target_execution_guards; risk public.risk_assessments; stamp timestamptz; until_at timestamptz;
BEGIN
 PERFORM access.job_state_context(p_member,p_user,p_job,'CLAIM');
 IF p_owner IS NULL OR p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 300 OR p_fence IS NULL OR p_fence<0
  OR p_version IS NULL OR p_version<1 THEN RAISE EXCEPTION 'invalid claim input' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(tenant::text,760208));
 PERFORM pg_advisory_xact_lock(hashtextextended(tenant::text,760210));
 SELECT * INTO job FROM public.command_jobs WHERE tenant_id=tenant AND id=p_job FOR UPDATE;
 stamp:=clock_timestamp();
 IF job.status<>'APPROVED' OR job.version<>p_version OR job.claimed_at IS NOT NULL
  OR job.dispatch_started_at IS NOT NULL OR job.expires_at<=stamp OR job.target_type<>'AD_PLAN' THEN
  RAISE EXCEPTION 'job cannot be claimed' USING ERRCODE='40001'; END IF;
 SELECT * INTO target FROM public.campaigns WHERE tenant_id=tenant AND id=job.campaign_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'target unavailable' USING ERRCODE='P0002'; END IF;
 INSERT INTO public.target_execution_guards(id,tenant_id,account_id,provider,product_line,target_type,native_id,state)
  VALUES(gen_random_uuid(),tenant,target.account_id,target.provider,target.product_line,'AD_PLAN',target.native_id,'IDLE')
  ON CONFLICT(tenant_id,account_id,provider,product_line,target_type,native_id) DO NOTHING;
 SELECT * INTO guard FROM public.target_execution_guards WHERE tenant_id=tenant AND account_id=target.account_id
  AND provider=target.provider AND product_line=target.product_line AND target_type='AD_PLAN'
  AND native_id=target.native_id FOR UPDATE;
 IF guard.state='QUARANTINED' THEN RAISE EXCEPTION 'target quarantined' USING ERRCODE='P0211'; END IF;
 IF guard.state<>'IDLE' THEN RAISE EXCEPTION 'target lease requires recovery' USING ERRCODE='55P03'; END IF;
 IF guard.fence<>p_fence THEN RAISE EXCEPTION 'old target fence' USING ERRCODE='40001'; END IF;
 SELECT * INTO risk FROM public.risk_assessments WHERE tenant_id=tenant AND id=p_assessment
  AND job_id=p_job AND user_id=p_user AND membership_id=p_member AND job_version=p_version
  AND stage='EXECUTE' AND checked_at<=stamp AND expires_at>stamp
  AND summary->>'result'='REVIEW_REQUIRED' AND summary->>'source'='SERVER'
  AND policy_version=(SELECT version FROM public.risk_policies WHERE tenant_id=tenant)
  AND jsonb_array_length(summary->'checks')>0 AND NOT EXISTS(
   SELECT 1 FROM jsonb_array_elements(summary->'checks') c WHERE c->>'passed' IS DISTINCT FROM 'true');
 IF NOT FOUND OR p_document->'risk_summary' IS DISTINCT FROM risk.summary THEN
  RAISE EXCEPTION 'fresh execution admission required' USING ERRCODE='22023'; END IF;
 -- 与取消共用既有条件状态内核；守卫失败时整个状态/事件/风险事务回滚。
 PERFORM access.job_apply_transition(p_member,p_user,p_job,p_version,'CLAIM',p_document,p_request,p_proof);
 until_at:=stamp+make_interval(secs=>p_seconds);
 UPDATE public.target_execution_guards SET state='LEASED',fence=fence+1,job_id=p_job,
  lease_owner=p_owner,lease_until=until_at WHERE tenant_id=tenant AND id=guard.id AND fence=p_fence
  AND state='IDLE' RETURNING * INTO guard;
 IF NOT FOUND THEN RAISE EXCEPTION 'claim predicate changed' USING ERRCODE='40001'; END IF;
 INSERT INTO public.target_guard_claims(id,tenant_id,guard_id,job_id,fence,lease_owner,lease_until,
  assessment_id,request_id,claimed_at) VALUES(gen_random_uuid(),tenant,guard.id,p_job,guard.fence,p_owner,
  until_at,p_assessment,p_request,stamp);
 RETURN jsonb_build_object('guardId',guard.id,'jobId',p_job,'fence',guard.fence,
  'owner',p_owner,'leaseUntil',until_at,'jobVersion',p_version+1);
END $$;
REVOKE ALL ON FUNCTION access.target_claim(uuid,uuid,uuid,bigint,bigint,uuid,integer,uuid,jsonb,uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.target_claim(uuid,uuid,uuid,bigint,bigint,uuid,integer,uuid,jsonb,uuid,text) TO app_login;

CREATE FUNCTION access.target_lease_current(p_member uuid,p_user uuid,p_job uuid,p_guard uuid,p_owner uuid,p_fence bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); guard public.target_execution_guards;
BEGIN
 PERFORM access.job_state_context(p_member,p_user,p_job,'CLAIM');
 SELECT * INTO guard FROM public.target_execution_guards WHERE tenant_id=tenant AND id=p_guard FOR SHARE;
 IF NOT FOUND OR guard.job_id IS DISTINCT FROM p_job OR guard.state<>'LEASED'
  OR guard.fence IS DISTINCT FROM p_fence OR guard.lease_owner IS DISTINCT FROM p_owner
  OR guard.lease_until<=clock_timestamp() THEN
  RAISE EXCEPTION 'target lease no longer current' USING ERRCODE='40001'; END IF;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION access.target_lease_current(uuid,uuid,uuid,uuid,uuid,bigint) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.target_lease_current(uuid,uuid,uuid,uuid,uuid,bigint) TO app_login;

CREATE FUNCTION access.target_guard_state(p_member uuid,p_user uuid,p_job uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant bigint:=public.current_tenant_id(); item jsonb; guard public.target_execution_guards;
BEGIN
 item:=access.job_state_context(p_member,p_user,p_job,'CLAIM');
 SELECT g.* INTO guard FROM public.campaigns c JOIN public.target_execution_guards g
  ON (g.tenant_id,g.account_id,g.provider,g.product_line,g.native_id)
   =(c.tenant_id,c.account_id,c.provider,c.product_line,c.native_id)
  WHERE c.tenant_id=tenant AND c.id=(item->>'campaign_id')::uuid AND g.target_type='AD_PLAN';
 IF NOT FOUND THEN RETURN jsonb_build_object('state','IDLE','fence',0); END IF;
 RETURN jsonb_build_object('guardId',guard.id,'state',guard.state,'fence',guard.fence,'leaseUntil',guard.lease_until);
END $$;
REVOKE ALL ON FUNCTION access.target_guard_state(uuid,uuid,uuid) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.target_guard_state(uuid,uuid,uuid) TO app_login;
