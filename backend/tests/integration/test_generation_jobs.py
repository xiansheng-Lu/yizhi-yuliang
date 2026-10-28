"""实际PG/MinIO生成账本；所有价格/供应商/费用回执为合成，不调用真实供应商。"""

from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.aikernel.cost_models import BillingOutcome
from yuliang.modules.content.generation_models import GenerationCreate, GenerationInput
from yuliang.modules.content.generation_provider import ProviderResult
from yuliang.modules.content.generation_service import GenerationService
from yuliang.modules.content.workflows_models import RunCreate, TemplateDefinition
from yuliang.modules.content.workflows_service import WorkflowService
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_configuration_context import configuration_ready as configuration_ready
from .test_content_assets import assets_ready as assets_ready
from .test_content_assets import graphic
from .test_content_uploads import blob as blob
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products

pytestmark = pytest.mark.integration


class SyntheticProvider:
    def __init__(self, d: dict[str, Any]) -> None:
        self.d = d
        self.sent: list[str] = []
        self.queries: list[str] = []
        self.timeout = False
        self.fail = False
        self.submit_billing = BillingOutcome()
        self.query_billing = BillingOutcome(
            state="KNOWN", amount_minor=1, currency="CNY", proof_hash="b" * 64
        )

    def submit(self, key: str, hash: str, parameters: dict[str, object]) -> ProviderResult:
        with business_transaction(self.d["engine"], self.d["identity"]) as s:
            row = s.execute(
                text("""SELECT j.submit_state,p.state FROM generation_jobs j
                JOIN ai_cost_permits p ON p.id=j.permit_id WHERE j.request_key=:key"""),
                dict(key=UUID(key)),
            ).one()
            assert tuple(row) == ("SENT", "IN_FLIGHT")
        self.sent.append(key)
        if self.timeout:
            raise TimeoutError("synthetic-response-lost")
        return ProviderResult(
            "FAILED" if self.fail else "ACKNOWLEDGED",
            external_id="synthetic-job",
            billing=self.submit_billing,
        )

    def query(self, key: str) -> ProviderResult:
        self.queries.append(key)
        return ProviderResult(
            "READY_FOR_REVIEW",
            external_id="synthetic-job",
            asset_id=self.d["asset"],
            billing=self.query_billing,
        )


@pytest.fixture
async def generations_ready(assets_ready: dict[str, Any]) -> dict[str, Any]:
    d = assets_ready
    workflow = WorkflowService(d["docs"])
    template = workflow.append_template(
        UUID("00000000-0000-4000-8000-000000000064"),
        TemplateDefinition.model_validate(
            dict(
                code="synthetic_generation",
                name="合成生成",
                schemaVersion=2,
                steps=[dict(code="generate", name="生成", kind="GENERATION")],
            )
        ),
    )
    run = workflow.create(
        RunCreate(templateId=template["id"], productId=d["product"], creationKey=uuid4().hex)
    )
    document = graphic(d, d["asset"]).model_copy(
        update=dict(id=run["rootDocumentId"], kind="planning")
    )
    saved = d["docs"].save(UUID(document.id), document)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""UPDATE ad_accounts SET entity_id=(SELECT entity_id FROM shops
            WHERE id=:shop) WHERE id=:account"""),
            dict(shop=d["shop"], account=d["detailctx"].account_id),
        )
        owner.execute(
            text("""INSERT INTO ai_cost_budgets(tenant_id,scenario,currency,period_start,
            period_end,limit_minor,status) VALUES(:tenant,'content_generation','CNY',
            now()-interval '1 day',now()+interval '1 day',100,'ACTIVE')"""),
            dict(tenant=d["tenant"]),
        )
        price = owner.execute(
            text("""INSERT INTO ai_price_versions(tenant_id,price_key,revision,kind,
            currency,unit,unit_scale,input_rate_micro_minor,output_rate_micro_minor,valid_from,
            valid_until,verification,proof_hash) VALUES(:tenant,'synthetic.job',1,'GENERATION',
            'CNY','JOB',1,1000000,0,now()-interval '1 day',now()+interval '1 day','VERIFIED',:proof)
            RETURNING id"""),
            dict(tenant=d["tenant"], proof="a" * 64),
        ).scalar_one()
        owner.execute(
            text("""INSERT INTO generation_price_sources(id,tenant_id,provider,kind,price_id,
            unit_semantic,proof_ref,source_sha256,checked_at,expires_at)
            VALUES(:id,:tenant,'synthetic',
            'IMAGE',:price,'ONE_REQUEST_JOB','synthetic-only-price-units',:sha,
            now()-interval '1 minute',now()+interval '1 day')"""),
            dict(id=uuid4(), tenant=d["tenant"], price=price, sha="a" * 64),
        )
    value = GenerationInput(
        requestKey=str(uuid4()),
        runId=run["id"],
        stepCode="generate",
        expectedRunVersion=1,
        expectedAttempt=1,
        documentId=run["rootDocumentId"],
        documentVersion=saved["version"],
        kind="IMAGE",
        provider="synthetic",
        priceKey="synthetic.job",
        prompt="合成端口技术验证，不代表实际生成产物",
    )
    provider = SyntheticProvider(d)
    return d | dict(
        generation=GenerationService(d["docs"], provider), provider=provider, input=value
    )


def confirmed(d: dict[str, Any], *, confirm: bool = True) -> GenerationCreate:
    q = d["generation"].quote(d["input"])
    value = GenerationCreate(
        input=d["input"],
        quoteId=q["quote_id"],
        quoteHash=q["quote_hash"],
        maximumMinor=q["max_cost_minor"],
        currency=q["currency"],
    )
    if confirm:
        d["generation"].confirm(value)
    return value


def ledger(d: dict[str, Any]) -> tuple[int, int, int]:
    with business_transaction(d["engine"], d["identity"]) as s:
        r = s.execute(
            text(
                "SELECT reserved_minor,settled_minor FROM ai_cost_budgets "
                "WHERE scenario='content_generation'"
            )
        ).one()
        calls = s.execute(text("SELECT count(*) FROM ai_invocations")).scalar_one()
        return int(r[0]), int(r[1]), calls


async def test_no_trusted_units_no_zero_quote(generations_ready: dict[str, Any]) -> None:
    d = generations_ready
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM generation_price_sources WHERE tenant_id=:tenant"),
            dict(tenant=d["tenant"]),
        )
    with pytest.raises(ApiError):
        d["generation"].quote(d["input"])
    assert ledger(d) == (0, 0, 0) and not d["provider"].sent


@pytest.mark.parametrize("failure", ["unconfirmed", "expired", "changed_quote"])
async def test_quote_confirmation_and_expiry_before_creation(
    generations_ready: dict[str, Any], failure: str
) -> None:
    d = generations_ready
    value = confirmed(d, confirm=failure != "unconfirmed")
    if failure == "expired":
        with d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text(
                    "UPDATE ai_cost_quotes SET expires_at=clock_timestamp()-interval '1 second' "
                    "WHERE id=:id"
                ),
                dict(id=UUID(value.quoteId)),
            )
    elif failure == "changed_quote":
        value = value.model_copy(update=dict(maximumMinor=value.maximumMinor + 1))
    with pytest.raises((ApiError, DBAPIError)):
        d["generation"].create(value)
    assert ledger(d) == (0, 0, 0) and not d["provider"].sent


async def test_stable_create_inflight_and_unsent_cancel(generations_ready: dict[str, Any]) -> None:
    d = generations_ready
    value = confirmed(d)
    first = d["generation"].create(value)
    assert first["status"] == "QUEUED" and first["submitState"] == "NOT_SENT"
    assert d["generation"].create(value) == first and ledger(d) == (1, 0, 0)
    altered = value.model_copy(
        update=dict(input=value.input.model_copy(update=dict(prompt="different")))
    )
    with pytest.raises(ApiError):
        d["generation"].create(altered)
    cancelled = d["generation"].cancel_unsent(UUID(first["id"]))
    assert cancelled["status"] == "CANCELLED" and ledger(d) == (0, 0, 0)
    assert d["generation"].cancel_unsent(UUID(first["id"])) == cancelled


async def test_sent_before_port_and_query_only_ready_review(
    generations_ready: dict[str, Any],
) -> None:
    d = generations_ready
    job = d["generation"].create(confirmed(d))
    identifier = UUID(job["id"])
    ack = d["generation"].dispatch(identifier)
    assert ack["status"] == "WAITING_PROVIDER" and ack["submitState"] == "ACKNOWLEDGED"
    assert ledger(d) == (1, 0, 0)
    assert d["generation"].dispatch(identifier) == ack and len(d["provider"].sent) == 1
    ready = d["generation"].query(identifier)
    assert ready["status"] == "WAITING_REVIEW" and ready["resultAssetId"] == str(d["asset"])
    assert ready["actualCostMinor"] == 1 and ledger(d) == (0, 1, 0)
    assert d["provider"].queries == d["provider"].sent == [d["input"].requestKey]


async def test_response_lost_unknown_never_resubmitted(generations_ready: dict[str, Any]) -> None:
    d = generations_ready
    job = d["generation"].create(confirmed(d))
    d["provider"].timeout = True
    identifier = UUID(job["id"])
    unknown = d["generation"].dispatch(identifier)
    assert unknown["submitState"] == "UNKNOWN" and unknown["actualCostMinor"] is None
    assert ledger(d) == (1, 0, 0)
    d["generation"].dispatch(identifier)
    assert len(d["provider"].sent) == 1
    with pytest.raises(ApiError):
        d["generation"].cancel_unsent(identifier)
    d["generation"].query(identifier)
    assert len(d["provider"].queries) == 1 and len(d["provider"].sent) == 1


async def test_crash_after_sent_before_result_no_second_submit(
    generations_ready: dict[str, Any],
) -> None:
    d = generations_ready
    job = d["generation"].create(confirmed(d))
    identifier = UUID(job["id"])
    with (
        patch.object(d["generation"], "_record", side_effect=RuntimeError("synthetic-crash")),
        pytest.raises(RuntimeError),
    ):
        d["generation"].dispatch(identifier)
    assert d["generation"].get(identifier)["submitState"] == "SENT"
    recovered = GenerationService(d["docs"], d["provider"])
    recovered.dispatch(identifier)
    assert len(d["provider"].sent) == 1 and ledger(d) == (1, 0, 0)
    assert recovered.query(identifier)["status"] == "WAITING_REVIEW"


async def test_atomic_create_fee_and_reference_failure(generations_ready: dict[str, Any]) -> None:
    d = generations_ready
    value = confirmed(d)
    with (
        patch.object(
            d["generation"].repo.assets,
            "reference_in_transaction",
            side_effect=RuntimeError("synthetic-ref-failure"),
        ),
        pytest.raises(RuntimeError),
    ):
        d["generation"].create(value)
    assert ledger(d) == (0, 0, 0)
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM generation_jobs")).scalar_one() == 0
    assert d["generation"].create(value)["status"] == "QUEUED"


async def test_current_document_change_blocks_sent(generations_ready: dict[str, Any]) -> None:
    d = generations_ready
    job = d["generation"].create(confirmed(d))
    identifier = UUID(job["id"])
    old = d["docs"].get(UUID(d["input"].documentId))
    from yuliang.modules.content.models import Document

    d["docs"].save(UUID(old["id"]), Document.model_validate(old | dict(body="current changed")))
    with pytest.raises(ApiError):
        d["generation"].dispatch(identifier)
    assert not d["provider"].sent and d["generation"].get(identifier)["submitState"] == "NOT_SENT"


async def test_default_provider_cannot_spend(generations_ready: dict[str, Any]) -> None:
    d = generations_ready
    job = d["generation"].create(confirmed(d))
    default = GenerationService(d["docs"])
    with pytest.raises(ApiError):
        default.dispatch(UUID(job["id"]))
    assert default.get(UUID(job["id"]))["submitState"] == "NOT_SENT" and ledger(d) == (1, 0, 0)


async def test_concurrent_same_key_and_database_inflight_constraint(
    generations_ready: dict[str, Any],
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    d = generations_ready
    value = confirmed(d)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: d["generation"].create(value), range(2)))
    assert jobs[0] == jobs[1] and ledger(d) == (1, 0, 0)
    with pytest.raises(DBAPIError), d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO generation_jobs
            SELECT (jsonb_populate_record(NULL::generation_jobs,to_jsonb(j)||
                jsonb_build_object('id',CAST(:id AS text),'request_key',CAST(:key AS text)))).*
            FROM generation_jobs j WHERE j.id=:old"""),
            dict(id=str(uuid4()), key=str(uuid4()), old=UUID(jobs[0]["id"])),
        )


@pytest.mark.parametrize("scope", ["actor", "tenant", "shop"])
async def test_generation_current_scope(generations_ready: dict[str, Any], scope: str) -> None:
    from yuliang.modules.content.service import ContentService
    from yuliang.platform.db import VerifiedIdentity

    d = generations_ready
    job = d["generation"].create(confirmed(d))
    identity = d["identity"]
    shop = d["shop"]
    if scope == "actor":
        identity = VerifiedIdentity(d["tenant"], identity.membership_id, uuid4())
    elif scope == "tenant":
        identity = VerifiedIdentity(d["tenant"] + 9999, identity.membership_id, identity.user_id)
    else:
        shop = uuid4()
    unauthorized = GenerationService(
        ContentService(d["engine"], identity, shop, blob=d["blob"]), d["provider"]
    )
    with pytest.raises(ApiError):
        unauthorized.get(UUID(job["id"]))
    assert not d["provider"].sent


async def test_actual_http_quote_confirm_create_get_cancel_and_csrf(
    generations_ready: dict[str, Any],
) -> None:
    import os

    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from yuliang.main import create_app

    from .test_infrastructure import local_url
    from .test_password_sessions import challenge, sign_in

    d = generations_ready
    settings = d["settings"].model_copy(
        update=dict(
            blob_enabled=True,
            blob_endpoint_url=local_url("YULIANG_TEST_S3_URL"),
            blob_bucket=d["blob"].bucket,
            blob_access_key=SecretStr("yuliang-local"),
            blob_secret_key=SecretStr(os.environ["YULIANG_TEST_S3_SECRET"]),
        )
    )
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        sign_in(d | dict(client=client))
        url = "/v1/content/generations"
        headers = {"X-Shop-Id": str(d["shop"]), "X-CSRF-Token": challenge(client)}
        missing = client.post(
            url + "/quotes", json=d["input"].model_dump(), headers={"X-Shop-Id": str(d["shop"])}
        )
        assert missing.status_code == 403
        quoted = client.post(url + "/quotes", json=d["input"].model_dump(), headers=headers)
        assert quoted.status_code == 200, quoted.text
        q = quoted.json()["data"]
        body = dict(
            input=d["input"].model_dump(),
            quoteId=q["quote_id"],
            quoteHash=q["quote_hash"],
            maximumMinor=q["max_cost_minor"],
            currency=q["currency"],
        )
        assert client.post(url + "/confirmations", json=body, headers=headers).status_code == 200
        created = client.post(url, json=body, headers=headers)
        assert created.status_code == 200, created.text
        job = created.json()["data"]
        assert (
            client.get(url + "/" + job["id"], headers=headers).json()["data"]["status"] == "QUEUED"
        )
        assert (
            client.get(url + "/" + job["id"] + "?tenantId=forged", headers=headers).status_code
            == 422
        )
        cancelled = client.post(url + "/" + job["id"] + "/cancel-unsent", headers=headers)
        assert cancelled.status_code == 200, cancelled.text
        assert ledger(d) == (0, 0, 0) and not d["provider"].sent


async def test_quote_actual_new_price_version_requires_own_source(
    generations_ready: dict[str, Any],
) -> None:
    d = generations_ready
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO ai_price_versions(tenant_id,price_key,revision,kind,currency,
            unit,unit_scale,input_rate_micro_minor,output_rate_micro_minor,valid_from,valid_until,
            verification,proof_hash) SELECT tenant_id,price_key,2,kind,currency,unit,unit_scale,
            input_rate_micro_minor,output_rate_micro_minor,valid_from,valid_until,verification,
            proof_hash FROM ai_price_versions WHERE tenant_id=:tenant
            AND price_key='synthetic.job'"""),
            dict(tenant=d["tenant"]),
        )
    with pytest.raises(ApiError):
        d["generation"].quote(d["input"])
    assert ledger(d) == (0, 0, 0)


async def test_revoked_unit_source_after_confirmation_cannot_create(
    generations_ready: dict[str, Any],
) -> None:
    d = generations_ready
    value = confirmed(d)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM generation_price_sources WHERE tenant_id=:tenant"),
            dict(tenant=d["tenant"]),
        )
    with pytest.raises(ApiError):
        d["generation"].create(value)
    assert ledger(d) == (0, 0, 0)


async def test_unverified_video_units_do_not_get_job_price(
    generations_ready: dict[str, Any],
) -> None:
    d = generations_ready
    with pytest.raises(ApiError):
        d["generation"].quote(d["input"].model_copy(update=dict(kind="VIDEO")))
    assert ledger(d) == (0, 0, 0)


async def test_failed_business_can_query_unknown_billing_without_resubmit(
    generations_ready: dict[str, Any],
) -> None:
    d = generations_ready
    d["provider"].fail = True
    job = d["generation"].create(confirmed(d))
    identifier = UUID(job["id"])
    failed = d["generation"].dispatch(identifier)
    assert (
        failed["status"] == "FAILED"
        and failed["billingState"] == "UNKNOWN"
        and ledger(d) == (1, 0, 0)
    )
    reconciled = d["generation"].query(identifier)
    assert reconciled["status"] == "FAILED" and reconciled["actualCostMinor"] == 1
    assert reconciled["resultAssetId"] is None and ledger(d) == (0, 1, 0)
    assert len(d["provider"].sent) == len(d["provider"].queries) == 1


async def test_known_billing_not_repeated_allows_new_asset_status(
    generations_ready: dict[str, Any],
) -> None:
    d = generations_ready
    d["provider"].submit_billing = d["provider"].query_billing
    d["provider"].query_billing = BillingOutcome()
    job = d["generation"].create(confirmed(d))
    identifier = UUID(job["id"])
    known = d["generation"].dispatch(identifier)
    assert known["status"] == "WAITING_PROVIDER" and known["billingState"] == "KNOWN"
    ready = d["generation"].query(identifier)
    assert ready["status"] == "WAITING_REVIEW" and ready["actualCostMinor"] == 1
    assert ledger(d) == (0, 1, 0)


async def test_new_contradictory_known_billing_rejected(generations_ready: dict[str, Any]) -> None:
    d = generations_ready
    d["provider"].submit_billing = d["provider"].query_billing
    job = d["generation"].create(confirmed(d))
    identifier = UUID(job["id"])
    first = d["generation"].dispatch(identifier)
    d["provider"].query_billing = BillingOutcome(
        state="KNOWN", amount_minor=2, currency="CNY", proof_hash="c" * 64
    )
    with pytest.raises(ApiError):
        d["generation"].query(identifier)
    assert d["generation"].get(identifier) == first and ledger(d) == (0, 1, 0)


@pytest.mark.parametrize(
    "forgery", ["unsent_success", "fake_sent", "missing_artifact", "known_rewrite"]
)
async def test_database_rejects_forged_lifecycle_and_fees(
    generations_ready: dict[str, Any], forgery: str
) -> None:
    d = generations_ready
    if forgery == "known_rewrite":
        d["provider"].submit_billing = d["provider"].query_billing
    job = d["generation"].create(confirmed(d))
    identifier = UUID(job["id"])
    if forgery in {"missing_artifact", "known_rewrite"}:
        d["generation"].dispatch(identifier)
    statement = {
        "unsent_success": "status='SUCCEEDED'",
        "fake_sent": "status='RUNNING',submit_state='SENT'",
        "missing_artifact": "status='WAITING_REVIEW',result_asset_id=:asset",
        "known_rewrite": "actual_cost_minor=2",
    }[forgery]
    with pytest.raises(DBAPIError), business_transaction(d["engine"], d["identity"]) as s:
        sql = "UPDATE generation_jobs SET " + statement + ",version=version+1 WHERE id=:id"  # noqa: S608 -- fixed internal cases
        s.execute(text(sql), dict(id=identifier, asset=uuid4()))
    actual = d["generation"].get(identifier)
    assert actual["status"] != "SUCCEEDED" and actual["resultAssetId"] is None
    assert actual["actualCostMinor"] == (1 if forgery == "known_rewrite" else None)


async def test_real_unknown_generation_blocks_ancestor_redo(
    generations_ready: dict[str, Any],
) -> None:
    from yuliang.modules.content.workflows_execution_models import Redo
    from yuliang.modules.content.workflows_execution_service import WorkflowExecution

    from .test_workflow_execution import accept_value, execute_value

    d = generations_ready
    workflow = WorkflowService(d["docs"])
    tpl = workflow.append_template(
        UUID("00000000-0000-4000-8000-000000000064"),
        TemplateDefinition.model_validate(
            dict(
                schemaVersion=2,
                code="ancestor_unknown",
                name="真实祖先重做拒绝",
                steps=[
                    dict(code="brief", name="人工", kind="MANUAL"),
                    dict(code="generate", name="生成", kind="GENERATION", dependsOn=["brief"]),
                ],
            )
        ),
    )
    run = workflow.create(
        RunCreate(templateId=tpl["id"], productId=d["product"], creationKey=uuid4().hex)
    )
    identifier = UUID(run["id"])
    execution = WorkflowExecution(d["docs"])
    proposed = execution.execute(
        identifier, "brief", execute_value(d, execution, identifier, "brief")
    )
    accepted = execution.accept(identifier, "brief", accept_value(proposed, "brief"))
    doc = d["docs"].get(UUID(run["rootDocumentId"]))
    d["input"] = d["input"].model_copy(
        update=dict(
            requestKey=str(uuid4()),
            runId=run["id"],
            expectedRunVersion=accepted["revision"],
            documentId=doc["id"],
            documentVersion=doc["version"],
        )
    )
    value = confirmed(d)
    generated = d["generation"].create(value)
    d["provider"].timeout = True
    unknown = d["generation"].dispatch(UUID(generated["id"]))
    assert unknown["submitState"] == "UNKNOWN"
    current = execution.get(identifier)
    with pytest.raises(ApiError):
        execution.redo(
            identifier,
            "brief",
            Redo(
                actionKey="redo-unknown",
                expectedRunVersion=current["revision"],
                expectedAttempt=1,
                reason="未知任务必须保留",
            ),
        )
    assert ledger(d) == (1, 0, 0) and len(d["provider"].sent) == 1


@pytest.mark.parametrize("action", ["send", "cancel"])
async def test_other_actor_cannot_forge_victim_dispatch(
    generations_ready: dict[str, Any], action: str
) -> None:
    from yuliang.platform.db import VerifiedIdentity

    d = generations_ready
    job = d["generation"].create(confirmed(d))
    with business_transaction(d["engine"], d["identity"]) as s:
        row = (
            s.execute(text("SELECT * FROM generation_jobs WHERE id=:id"), dict(id=UUID(job["id"])))
            .mappings()
            .one()
        )
        args = dict(
            tenant=d["tenant"], job=row["id"], key=row["request_key"], hash=row["payload_hash"]
        )
    attacker = VerifiedIdentity(d["tenant"], d["approver_member"], d["approver"])
    with pytest.raises(DBAPIError) as denied, business_transaction(d["engine"], attacker) as s:
        s.execute(
            text("""INSERT INTO generation_dispatches(tenant_id,job_id,attempt_no,
            request_key,payload_hash)
            VALUES(:tenant,:job,1,:key,:hash)"""),
            args,
        )
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    if action == "send":
        assert d["generation"].dispatch(UUID(job["id"]))["submitState"] == "ACKNOWLEDGED"
        with business_transaction(d["engine"], attacker) as s:
            assert (
                s.execute(
                    text("SELECT count(*) FROM generation_dispatches WHERE job_id=:job"), args
                ).scalar_one()
                == 0
            )
    else:
        assert d["generation"].cancel_unsent(UUID(job["id"]))["status"] == "CANCELLED"
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(
            text("SELECT count(*) FROM generation_dispatches WHERE job_id=:job"), args
        ).scalar_one() == (1 if action == "send" else 0)


@pytest.mark.parametrize("action", ["cancel", "failed_billing"])
async def test_barrier_create_other_ready_step_vs_cancel_or_failed_settlement(
    generations_ready: dict[str, Any], action: str
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    d = generations_ready
    workflow = WorkflowService(d["docs"])
    template = workflow.append_template(
        UUID("00000000-0000-4000-8000-000000000064"),
        TemplateDefinition.model_validate(
            dict(
                schemaVersion=2,
                code="parallel_generation",
                name="真实独立READY分支",
                steps=[
                    dict(code=code, name=code, kind="GENERATION")
                    for code in ("generate_a", "generate_b")
                ],
            )
        ),
    )
    run = workflow.create(
        RunCreate(templateId=template["id"], productId=d["product"], creationKey=uuid4().hex)
    )
    document = graphic(d, d["asset"]).model_copy(
        update=dict(id=run["rootDocumentId"], kind="planning")
    )
    saved = d["docs"].save(UUID(document.id), document)
    first_input = d["input"].model_copy(
        update=dict(
            requestKey=str(uuid4()),
            runId=run["id"],
            stepCode="generate_a",
            documentId=document.id,
            documentVersion=saved["version"],
        )
    )
    first = d["generation"].create(confirmed(d | dict(input=first_input)))
    first_id = UUID(first["id"])
    if action == "failed_billing":
        d["generation"].dispatch(first_id)
    second_input = first_input.model_copy(
        update=dict(requestKey=str(uuid4()), stepCode="generate_b", expectedRunVersion=2)
    )
    second = confirmed(d | dict(input=second_input))
    barrier = Barrier(2)
    service: GenerationService = d["generation"]

    def create() -> dict[str, Any] | None:
        barrier.wait(timeout=10)
        try:
            return service.create(second)
        except ApiError as error:
            assert error.code.value == "VERSION_CONFLICT"
            return None

    def finish() -> dict[str, Any]:
        barrier.wait(timeout=10)
        if action == "cancel":
            return service.cancel_unsent(first_id)
        return service._record(
            first_id,
            ProviderResult(
                "FAILED",
                external_id="synthetic-job",
                billing=BillingOutcome(
                    state="KNOWN", amount_minor=1, currency="CNY", proof_hash="b" * 64
                ),
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(create), pool.submit(finish)
        created, finished = a.result(timeout=20), b.result(timeout=20)
    assert finished["status"] == ("CANCELLED" if action == "cancel" else "FAILED")
    if created is None:
        from yuliang.modules.content.workflows_execution_service import WorkflowExecution

        current_revision = WorkflowExecution(d["docs"]).get(UUID(run["id"]))["revision"]
        retry = second_input.model_copy(
            update=dict(requestKey=str(uuid4()), expectedRunVersion=current_revision)
        )
        created = d["generation"].create(confirmed(d | dict(input=retry)))
    assert created["status"] == "QUEUED"
    assert ledger(d) == (1, 0 if action == "cancel" else 1, 0)
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM generation_jobs WHERE run_id=:run"),
                dict(run=UUID(run["id"])),
            ).scalar_one()
            == 2
        )
