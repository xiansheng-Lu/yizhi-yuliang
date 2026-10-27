CREATE TABLE ai_cost_budgets (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id bigint NOT NULL REFERENCES tenants(id),
 scenario varchar(40) NOT NULL,currency varchar(3) NOT NULL CHECK(currency IN ('CNY','USD')),
 period_start timestamptz NOT NULL,period_end timestamptz NOT NULL CHECK(period_end>period_start),
 limit_minor numeric(30,0) NOT NULL CHECK(limit_minor>=0),
 reserved_minor numeric(30,0) NOT NULL DEFAULT 0 CHECK(reserved_minor>=0),
 settled_minor numeric(30,0) NOT NULL DEFAULT 0 CHECK(settled_minor>=0),
 status varchar(8) NOT NULL CHECK(status IN ('ACTIVE','FROZEN')),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,scenario,currency,period_start)
);
CREATE TABLE ai_price_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id bigint NOT NULL REFERENCES tenants(id),
 price_key varchar(80) NOT NULL,revision integer NOT NULL CHECK(revision>0),
 kind varchar(10) NOT NULL CHECK(kind IN ('LLM','GENERATION')),
 currency varchar(3) NOT NULL CHECK(currency IN ('CNY','USD')),
 unit varchar(10) NOT NULL CHECK(unit IN ('TOKEN','JOB','SECOND')),
 unit_scale bigint NOT NULL CHECK(unit_scale IN (1,1000,1000000)),
 input_rate_micro_minor bigint NOT NULL CHECK(input_rate_micro_minor>=0),
 output_rate_micro_minor bigint NOT NULL CHECK(output_rate_micro_minor>=0),
 valid_from timestamptz NOT NULL,valid_until timestamptz NOT NULL CHECK(valid_until>valid_from),
 verification varchar(8) NOT NULL CHECK(verification IN ('DRAFT','VERIFIED','REVOKED')),
 proof_hash varchar(64) NOT NULL CHECK(proof_hash ~ '^[0-9a-f]{64}$'),
 CHECK((kind='LLM' AND unit='TOKEN') OR (kind='GENERATION' AND unit IN ('JOB','SECOND'))),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,kind,price_key,revision)
);
CREATE TABLE ai_cost_quotes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id bigint NOT NULL REFERENCES tenants(id),
 actor_id uuid NOT NULL REFERENCES users(id),request_key uuid NOT NULL,
 kind varchar(10) NOT NULL CHECK(kind IN ('LLM','GENERATION')),scenario varchar(40) NOT NULL,
 price_id uuid NOT NULL,budget_id uuid NOT NULL,
 request_fingerprint varchar(64) NOT NULL CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),
 price_snapshot jsonb NOT NULL,max_cost_minor bigint NOT NULL CHECK(max_cost_minor>=0),
 currency varchar(3) NOT NULL CHECK(currency IN ('CNY','USD')),
 quote_hash varchar(64) NOT NULL CHECK(quote_hash ~ '^[0-9a-f]{64}$'),
 expires_at timestamptz NOT NULL,confirmed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,price_id) REFERENCES ai_price_versions(tenant_id,id),
 FOREIGN KEY(tenant_id,budget_id) REFERENCES ai_cost_budgets(tenant_id,id)
);
CREATE TABLE ai_cost_permits (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id bigint NOT NULL REFERENCES tenants(id),
 actor_id uuid NOT NULL REFERENCES users(id),quote_id uuid NOT NULL,
 request_key uuid NOT NULL,kind varchar(10) NOT NULL CHECK(kind IN ('LLM','GENERATION')),
 token_hash varchar(64) NOT NULL CHECK(token_hash ~ '^[0-9a-f]{64}$'),
 state varchar(12) NOT NULL DEFAULT 'RESERVED'
  CHECK(state IN ('RESERVED','IN_FLIGHT','UNKNOWN','SETTLED','CANCELLED')),
 reported_cost_minor bigint CHECK(reported_cost_minor>=0),reported_currency varchar(3),
 settled_cost_minor bigint CHECK(settled_cost_minor>=0),proof_hash varchar(64),
 anomaly varchar(30),created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,quote_id),UNIQUE(tenant_id,kind,request_key),
 FOREIGN KEY(tenant_id,quote_id) REFERENCES ai_cost_quotes(tenant_id,id),
 CHECK((state='SETTLED' AND settled_cost_minor IS NOT NULL AND proof_hash IS NOT NULL) OR
       (state<>'SETTLED' AND settled_cost_minor IS NULL)),
 CHECK(reported_currency IS NULL OR reported_currency IN ('CNY','USD')),
 CHECK(proof_hash IS NULL OR proof_hash ~ '^[0-9a-f]{64}$')
);
CREATE TABLE ai_cost_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,tenant_id bigint NOT NULL REFERENCES tenants(id),
 actor_id uuid NOT NULL REFERENCES users(id),permit_id uuid NOT NULL,
 event varchar(20) NOT NULL CHECK(event IN ('RESERVED','IN_FLIGHT','UNKNOWN','SETTLED','CANCELLED','OVERAGE','CURRENCY_MISMATCH')),
 reported_cost_minor bigint CHECK(reported_cost_minor>=0),reported_currency varchar(3),proof_hash varchar(64),
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((reported_cost_minor IS NULL AND reported_currency IS NULL AND proof_hash IS NULL) OR
       (reported_cost_minor IS NOT NULL AND reported_currency IS NOT NULL AND reported_currency IN ('CNY','USD')
        AND proof_hash IS NOT NULL AND proof_hash ~ '^[0-9a-f]{64}$')),
 FOREIGN KEY(tenant_id,permit_id) REFERENCES ai_cost_permits(tenant_id,id)
);
DO $$ DECLARE target text; policy text; BEGIN
 FOREACH target IN ARRAY ARRAY['ai_cost_budgets','ai_price_versions','ai_cost_quotes','ai_cost_permits','ai_cost_events'] LOOP
  policy:=CASE WHEN target IN ('ai_cost_budgets','ai_price_versions') THEN
   'tenant_id=public.current_tenant_id()' ELSE
   'tenant_id=public.current_tenant_id() AND actor_id=public.current_actor_id()' END;
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',target);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',target);
  EXECUTE format('CREATE POLICY fee_owner ON public.%I TO app_owner USING(true) WITH CHECK(true)',target);
  EXECUTE format('CREATE POLICY fee_read ON public.%I FOR SELECT TO app_login USING(%s)',target,policy);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,app_login,app_auth',target);
  EXECUTE format('GRANT SELECT ON public.%I TO app_login',target);
 END LOOP;
END; $$;

CREATE FUNCTION access.ai_fee_actor(p_member uuid,p_kind text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.tenants t ON t.id=m.tenant_id
  JOIN public.role_grants g ON (g.tenant_id,g.membership_id)=(m.tenant_id,m.id)
  JOIN public.role_permissions p ON (p.tenant_id,p.role_id)=(g.tenant_id,g.role_id)
  WHERE m.id=p_member AND m.tenant_id=public.current_tenant_id() AND m.user_id=public.current_actor_id()
   AND m.status='ACTIVE' AND u.status='ACTIVE' AND u.deleted_at IS NULL
   AND t.status='ACTIVE' AND t.deleted_at IS NULL
   AND (p.permission_code='content.write' OR (p_kind='LLM' AND p.permission_code='task.create')));
$$;
REVOKE ALL ON FUNCTION access.ai_fee_actor(uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.ai_fee_actor(uuid,text) TO app_login;

CREATE FUNCTION access.ai_fee_quote(p_member uuid,p_request uuid,p_kind text,p_scenario text,
 p_price text,p_input bigint,p_output bigint,p_fingerprint text)
RETURNS TABLE(quote_id uuid,request_key uuid,max_cost_minor bigint,currency varchar,expires_at timestamptz,quote_hash varchar)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE price public.ai_price_versions%ROWTYPE; budget public.ai_cost_budgets%ROWTYPE;
 budgets uuid[]; amount numeric; expiry timestamptz; snapshot jsonb; signature varchar; created uuid;
BEGIN
 IF NOT access.ai_fee_actor(p_member,p_kind) THEN
  RAISE EXCEPTION 'fee permission denied' USING ERRCODE='42501'; END IF;
 IF p_request IS NULL OR p_kind IS NULL OR p_scenario IS NULL OR p_price IS NULL
  OR p_input IS NULL OR p_output IS NULL OR p_fingerprint IS NULL
  OR p_scenario !~ '^[a-z][a-z0-9_.-]{0,39}$'
  OR p_kind NOT IN ('LLM','GENERATION') OR p_input<0 OR p_output<0
  OR p_input>1000000000 OR p_output>1000000000 OR p_fingerprint !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'invalid billable request' USING ERRCODE='22023'; END IF;
 SELECT * INTO price FROM public.ai_price_versions WHERE tenant_id=public.current_tenant_id()
  AND price_key=p_price AND kind=p_kind AND verification='VERIFIED'
  AND valid_from<=statement_timestamp() AND valid_until>statement_timestamp() ORDER BY revision DESC LIMIT 1;
 IF NOT FOUND THEN RAISE EXCEPTION 'verified price unavailable' USING ERRCODE='22023'; END IF;
 IF p_kind='LLM' AND NOT EXISTS(SELECT 1 FROM public.ai_invocations WHERE tenant_id=public.current_tenant_id()
  AND call_id=p_request AND user_id=public.current_actor_id() AND status='STARTED'
  AND model_key=p_price AND scenario=p_scenario AND billing_currency=price.currency) THEN
  RAISE EXCEPTION 'gateway request mismatch' USING ERRCODE='42501'; END IF;
 SELECT array_agg(id) INTO budgets FROM public.ai_cost_budgets WHERE tenant_id=public.current_tenant_id()
  AND scenario=p_scenario AND ai_cost_budgets.currency=price.currency AND status='ACTIVE'
  AND period_start<=statement_timestamp() AND period_end>statement_timestamp();
 IF coalesce(cardinality(budgets),0)<>1 THEN
  RAISE EXCEPTION 'budget unavailable or ambiguous' USING ERRCODE='22023'; END IF;
 SELECT * INTO budget FROM public.ai_cost_budgets WHERE id=budgets[1];
 amount:=ceil((p_input::numeric*price.input_rate_micro_minor+p_output::numeric*price.output_rate_micro_minor)
              /(1000000::numeric*price.unit_scale));
 IF amount>9223372036854775807 THEN RAISE EXCEPTION 'quote exceeds range' USING ERRCODE='22023'; END IF;
 expiry:=least(statement_timestamp()+interval '5 minutes',price.valid_until,budget.period_end);
 snapshot:=jsonb_build_object('price_id',price.id,'revision',price.revision,'price_key',price.price_key,
  'unit',price.unit,'unit_scale',price.unit_scale,'input_rate_micro_minor',price.input_rate_micro_minor,
  'output_rate_micro_minor',price.output_rate_micro_minor,'input_units',p_input,'output_units',p_output,
  'proof_hash',price.proof_hash,'currency',price.currency);
 signature:=encode(sha256(convert_to(jsonb_build_object('request',p_request,'fingerprint',p_fingerprint,
  'price',snapshot,'max_cost_minor',amount,'budget',budget.id,'expires_at',expiry)::text,'UTF8')),'hex');
 INSERT INTO public.ai_cost_quotes(tenant_id,actor_id,request_key,kind,scenario,price_id,budget_id,
  request_fingerprint,price_snapshot,max_cost_minor,currency,quote_hash,expires_at)
 VALUES(public.current_tenant_id(),public.current_actor_id(),p_request,p_kind,p_scenario,price.id,budget.id,
  p_fingerprint,snapshot,amount,price.currency,signature,expiry) RETURNING id INTO created;
 RETURN QUERY SELECT created,p_request,amount::bigint,price.currency,expiry,signature;
END; $$;

CREATE FUNCTION access.ai_fee_confirm(p_member uuid,p_quote uuid,p_hash text,p_max bigint,p_currency text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE q public.ai_cost_quotes%ROWTYPE;
BEGIN
 SELECT * INTO q FROM public.ai_cost_quotes WHERE id=p_quote AND tenant_id=public.current_tenant_id()
  AND actor_id=public.current_actor_id() FOR UPDATE;
 IF NOT FOUND OR p_hash IS NULL OR p_max IS NULL OR p_currency IS NULL
  OR NOT access.ai_fee_actor(p_member,q.kind) OR q.expires_at<=statement_timestamp()
  OR q.quote_hash<>p_hash OR q.max_cost_minor<>p_max OR q.currency<>p_currency THEN RETURN false; END IF;
 UPDATE public.ai_cost_quotes SET confirmed_at=coalesce(confirmed_at,statement_timestamp()) WHERE id=q.id;
 RETURN true;
END; $$;

CREATE FUNCTION access.ai_fee_acquire(p_member uuid,p_quote uuid,p_token text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE q public.ai_cost_quotes%ROWTYPE; budget public.ai_cost_budgets%ROWTYPE; created uuid;
BEGIN
 SELECT * INTO q FROM public.ai_cost_quotes WHERE id=p_quote AND tenant_id=public.current_tenant_id()
  AND actor_id=public.current_actor_id();
 IF NOT FOUND THEN RAISE EXCEPTION 'quote denied' USING ERRCODE='42501'; END IF;
 -- 所有费用变更统一先锁预算，再锁报价/许可；跨进程并发不靠Redis或向量检索。
 SELECT * INTO budget FROM public.ai_cost_budgets WHERE id=q.budget_id FOR UPDATE;
 SELECT * INTO q FROM public.ai_cost_quotes WHERE id=p_quote FOR UPDATE;
 IF NOT access.ai_fee_actor(p_member,q.kind) OR q.confirmed_at IS NULL OR q.expires_at<=statement_timestamp()
  OR budget.status<>'ACTIVE' OR budget.period_end<=statement_timestamp()
  OR NOT EXISTS(SELECT 1 FROM public.ai_price_versions WHERE id=q.price_id AND verification='VERIFIED'
    AND valid_until>statement_timestamp()) OR p_token !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'unconfirmed or expired quote' USING ERRCODE='22023'; END IF;
 IF budget.reserved_minor+budget.settled_minor+q.max_cost_minor>budget.limit_minor THEN
  RAISE EXCEPTION 'cost budget exhausted' USING ERRCODE='P0001'; END IF;
 UPDATE public.ai_cost_budgets SET reserved_minor=reserved_minor+q.max_cost_minor WHERE id=budget.id;
 INSERT INTO public.ai_cost_permits(tenant_id,actor_id,quote_id,request_key,kind,token_hash)
 VALUES(q.tenant_id,q.actor_id,q.id,q.request_key,q.kind,p_token) RETURNING id INTO created;
 INSERT INTO public.ai_cost_events(tenant_id,actor_id,permit_id,event) VALUES(q.tenant_id,q.actor_id,created,'RESERVED');
 RETURN created;
END; $$;

CREATE FUNCTION access.ai_fee_start(p_member uuid,p_permit uuid,p_token text,p_fingerprint text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE permit public.ai_cost_permits%ROWTYPE; q public.ai_cost_quotes%ROWTYPE; budget public.ai_cost_budgets%ROWTYPE;
BEGIN
 SELECT p.* INTO permit FROM public.ai_cost_permits p WHERE p.id=p_permit
  AND p.tenant_id=public.current_tenant_id() AND p.actor_id=public.current_actor_id() AND p.token_hash=p_token;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO q FROM public.ai_cost_quotes WHERE id=permit.quote_id;
 SELECT * INTO budget FROM public.ai_cost_budgets WHERE id=q.budget_id FOR UPDATE;
 SELECT * INTO permit FROM public.ai_cost_permits WHERE id=p_permit FOR UPDATE;
 IF p_fingerprint IS NULL OR permit.state<>'RESERVED' OR NOT access.ai_fee_actor(p_member,permit.kind)
  OR q.request_fingerprint<>p_fingerprint OR q.expires_at<=statement_timestamp()
  OR q.confirmed_at IS NULL OR budget.status<>'ACTIVE' OR budget.period_end<=statement_timestamp()
  OR budget.reserved_minor+budget.settled_minor>budget.limit_minor
  OR NOT EXISTS(SELECT 1 FROM public.ai_price_versions WHERE id=q.price_id AND verification='VERIFIED'
     AND valid_until>statement_timestamp()) THEN RETURN false; END IF;
 IF permit.kind='LLM' AND NOT EXISTS(SELECT 1 FROM public.ai_invocations
  WHERE tenant_id=permit.tenant_id AND user_id=permit.actor_id AND call_id=permit.request_key
   AND status='STARTED' AND model_key=q.price_snapshot->>'price_key'
   AND scenario=q.scenario AND billing_currency=q.currency) THEN RETURN false; END IF;
 UPDATE public.ai_cost_permits SET state='IN_FLIGHT',updated_at=statement_timestamp() WHERE id=permit.id;
 INSERT INTO public.ai_cost_events(tenant_id,actor_id,permit_id,event) VALUES(permit.tenant_id,permit.actor_id,permit.id,'IN_FLIGHT');
 RETURN true;
END; $$;

CREATE FUNCTION access.ai_fee_finish(p_permit uuid,p_token text,p_state text,p_amount bigint,p_currency text,p_proof text)
RETURNS varchar LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE permit public.ai_cost_permits%ROWTYPE; q public.ai_cost_quotes%ROWTYPE; budget public.ai_cost_budgets%ROWTYPE; label text;
BEGIN
 SELECT p.* INTO permit FROM public.ai_cost_permits p WHERE p.id=p_permit
  AND p.tenant_id=public.current_tenant_id() AND p.actor_id=public.current_actor_id() AND p.token_hash=p_token;
 IF NOT FOUND THEN RAISE EXCEPTION 'permit denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO q FROM public.ai_cost_quotes WHERE id=permit.quote_id;
 SELECT * INTO budget FROM public.ai_cost_budgets WHERE id=q.budget_id FOR UPDATE;
 SELECT * INTO permit FROM public.ai_cost_permits WHERE id=p_permit FOR UPDATE;
 IF permit.state='SETTLED' THEN
  IF p_state='KNOWN' AND permit.settled_cost_minor=p_amount AND permit.reported_currency=p_currency
    AND permit.proof_hash=p_proof THEN RETURN 'SETTLED'; END IF;
  RAISE EXCEPTION 'settlement immutable' USING ERRCODE='22023'; END IF;
 IF permit.state NOT IN ('IN_FLIGHT','UNKNOWN') THEN
  RAISE EXCEPTION 'permit not in flight' USING ERRCODE='22023'; END IF;
 IF p_state='UNKNOWN' AND p_amount IS NULL AND p_currency IS NULL AND p_proof IS NULL THEN
  IF permit.state<>'UNKNOWN' THEN
   UPDATE public.ai_cost_permits SET state='UNKNOWN',updated_at=statement_timestamp() WHERE id=permit.id;
   INSERT INTO public.ai_cost_events(tenant_id,actor_id,permit_id,event) VALUES(permit.tenant_id,permit.actor_id,permit.id,'UNKNOWN');
  END IF;
  RETURN 'UNKNOWN'; END IF;
 IF p_state IS NULL OR p_state<>'KNOWN' OR p_amount IS NULL OR p_amount<0
   OR p_currency IS NULL OR p_currency NOT IN ('CNY','USD')
   OR p_proof IS NULL OR p_proof !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'valid billing receipt required' USING ERRCODE='22023'; END IF;
 IF p_currency<>q.currency THEN
  IF permit.state='UNKNOWN' AND permit.reported_cost_minor=p_amount
    AND permit.reported_currency=p_currency AND permit.proof_hash=p_proof THEN RETURN 'UNKNOWN'; END IF;
  UPDATE public.ai_cost_permits SET state='UNKNOWN',reported_cost_minor=p_amount,reported_currency=p_currency,
   proof_hash=p_proof,anomaly='CURRENCY_MISMATCH',updated_at=statement_timestamp() WHERE id=permit.id;
  INSERT INTO public.ai_cost_events(tenant_id,actor_id,permit_id,event,reported_cost_minor,reported_currency,proof_hash)
   VALUES(permit.tenant_id,permit.actor_id,permit.id,'CURRENCY_MISMATCH',p_amount,p_currency,p_proof);
  RETURN 'UNKNOWN'; END IF;
 UPDATE public.ai_cost_budgets SET reserved_minor=reserved_minor-q.max_cost_minor,
   settled_minor=settled_minor+p_amount WHERE id=budget.id;
 label:=CASE WHEN p_amount>q.max_cost_minor THEN 'OVERAGE' ELSE 'SETTLED' END;
 UPDATE public.ai_cost_permits SET state='SETTLED',reported_cost_minor=p_amount,reported_currency=p_currency,
  settled_cost_minor=p_amount,proof_hash=p_proof,anomaly=CASE WHEN label='OVERAGE' THEN 'OVERAGE' ELSE NULL END,
  updated_at=statement_timestamp() WHERE id=permit.id;
 INSERT INTO public.ai_cost_events(tenant_id,actor_id,permit_id,event,reported_cost_minor,reported_currency,proof_hash)
  VALUES(permit.tenant_id,permit.actor_id,permit.id,label,p_amount,p_currency,p_proof);
 RETURN 'SETTLED';
END; $$;

CREATE FUNCTION access.ai_fee_cancel(p_permit uuid,p_token text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE permit public.ai_cost_permits%ROWTYPE; q public.ai_cost_quotes%ROWTYPE;
BEGIN
 SELECT p.* INTO permit FROM public.ai_cost_permits p WHERE p.id=p_permit
  AND p.tenant_id=public.current_tenant_id() AND p.actor_id=public.current_actor_id() AND p.token_hash=p_token;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO q FROM public.ai_cost_quotes WHERE id=permit.quote_id;
 PERFORM 1 FROM public.ai_cost_budgets WHERE id=q.budget_id FOR UPDATE;
 SELECT * INTO permit FROM public.ai_cost_permits WHERE id=p_permit FOR UPDATE;
 IF permit.state<>'RESERVED' THEN RETURN false; END IF;
 UPDATE public.ai_cost_budgets SET reserved_minor=reserved_minor-q.max_cost_minor WHERE id=q.budget_id;
 UPDATE public.ai_cost_permits SET state='CANCELLED',updated_at=statement_timestamp() WHERE id=permit.id;
 INSERT INTO public.ai_cost_events(tenant_id,actor_id,permit_id,event) VALUES(permit.tenant_id,permit.actor_id,permit.id,'CANCELLED');
 RETURN true;
END; $$;

REVOKE ALL ON FUNCTION access.ai_fee_quote(uuid,uuid,text,text,text,bigint,bigint,text),
 access.ai_fee_confirm(uuid,uuid,text,bigint,text),access.ai_fee_acquire(uuid,uuid,text),
 access.ai_fee_start(uuid,uuid,text,text),access.ai_fee_finish(uuid,text,text,bigint,text,text),
 access.ai_fee_cancel(uuid,text) FROM PUBLIC,app_auth;
GRANT EXECUTE ON FUNCTION access.ai_fee_quote(uuid,uuid,text,text,text,bigint,bigint,text),
 access.ai_fee_confirm(uuid,uuid,text,bigint,text),access.ai_fee_acquire(uuid,uuid,text),
 access.ai_fee_start(uuid,uuid,text,text),access.ai_fee_finish(uuid,text,text,bigint,text,text),
 access.ai_fee_cancel(uuid,text) TO app_login;
