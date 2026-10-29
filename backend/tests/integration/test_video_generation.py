"""实际PG/MinIO视频编排骨架；供应商、价格与费用回执全部为合成，不调用真实视频服务。"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.aikernel.cost_models import BillingOutcome
from yuliang.modules.content.generation_models import GenerationCreate, GenerationInput
from yuliang.modules.content.video_generation_models import VideoTaskResult
from yuliang.modules.content.video_generation_provider import UnconfiguredVideoProvider
from yuliang.modules.content.video_generation_service import VideoGenerationService
from yuliang.modules.content.workflows_models import RunCreate, TemplateDefinition
from yuliang.modules.content.workflows_service import WorkflowService
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .fake_video_provider import FakeVideoProvider
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


@pytest.fixture
async def video_ready(assets_ready: dict[str, Any]) -> dict[str, Any]:
    d = assets_ready
    workflow = WorkflowService(d["docs"])
    template = workflow.append_template(
        UUID("00000000-0000-4000-8000-000000000066"),
        TemplateDefinition.model_validate(
            dict(
                code="synthetic_video_generation",
                name="合成视频生成",
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
        second_price = owner.execute(
            text("""INSERT INTO ai_price_versions(tenant_id,price_key,revision,kind,
            currency,unit,unit_scale,input_rate_micro_minor,output_rate_micro_minor,valid_from,
            valid_until,verification,proof_hash)
            VALUES(:tenant,'synthetic.video.second',1,'GENERATION','CNY','SECOND',1,
            1000000,0,now()-interval '1 day',now()+interval '1 day','VERIFIED',:proof)
            RETURNING id"""),
            dict(tenant=d["tenant"], proof="c" * 64),
        ).scalar_one()
        call_price = owner.execute(
            text("""INSERT INTO ai_price_versions(tenant_id,price_key,revision,kind,
            currency,unit,unit_scale,input_rate_micro_minor,output_rate_micro_minor,valid_from,
            valid_until,verification,proof_hash)
            VALUES(:tenant,'synthetic.video.call',1,'GENERATION','CNY','JOB',1,
            1000000,0,now()-interval '1 day',now()+interval '1 day','VERIFIED',:proof)
            RETURNING id"""),
            dict(tenant=d["tenant"], proof="e" * 64),
        ).scalar_one()
        for source_provider, source_price, semantic, sha in (
            ("fake-video", second_price, "ONE_REQUEST_SECOND", "c" * 64),
            ("fake-video-call", call_price, "ONE_REQUEST_JOB", "e" * 64),
        ):
            owner.execute(
                text("""INSERT INTO generation_price_sources(id,tenant_id,provider,kind,
                price_id,unit_semantic,proof_ref,source_sha256,checked_at,expires_at)
                VALUES(:id,:tenant,:provider,'VIDEO',:price,:semantic,
                'synthetic-video-only-price-units',:sha,
                now()-interval '1 minute',now()+interval '1 day')"""),
                dict(
                    id=uuid4(),
                    tenant=d["tenant"],
                    provider=source_provider,
                    price=source_price,
                    semantic=semantic,
                    sha=sha,
                ),
            )
    common = dict(
        requestKey=str(uuid4()),
        runId=run["id"],
        stepCode="generate",
        expectedRunVersion=1,
        expectedAttempt=1,
        documentId=run["rootDocumentId"],
        documentVersion=saved["version"],
        kind="VIDEO",
        prompt="合成视频端口技术验证，不代表实际产物",
    )
    second_input = GenerationInput.model_validate(
        common | dict(provider="fake-video", priceKey="synthetic.video.second", durationSeconds=5)
    )
    call_input = GenerationInput.model_validate(
        common
        | dict(
            requestKey=str(uuid4()),
            provider="fake-video-call",
            priceKey="synthetic.video.call",
        )
    )
    provider = FakeVideoProvider()
    service = VideoGenerationService(d["docs"], provider)
    return d | dict(
        video=service,
        videoProvider=provider,
        videoInput=second_input,
        callInput=call_input,
    )


def confirmed(d: dict[str, Any], value: GenerationInput) -> GenerationCreate:
    quote = d["video"].quote(value)
    accepted = GenerationCreate(
        input=value,
        quoteId=quote["quote_id"],
        quoteHash=quote["quote_hash"],
        maximumMinor=quote["max_cost_minor"],
        currency=quote["currency"],
    )
    d["video"].confirm(accepted)
    return accepted


def created(d: dict[str, Any], value: GenerationInput | None = None) -> None:
    d["video"].create(confirmed(d, d["videoInput"] if value is None else value))


def ledger(d: dict[str, Any]) -> tuple[int, int, int]:
    with business_transaction(d["engine"], d["identity"]) as s:
        row = s.execute(
            text(
                "SELECT reserved_minor,settled_minor FROM ai_cost_budgets "
                "WHERE scenario='content_generation'"
            )
        ).one()
        calls = s.execute(text("SELECT count(*) FROM ai_invocations")).scalar_one()
        return int(row[0]), int(row[1]), calls


def job_id(d: dict[str, Any]) -> UUID:
    with business_transaction(d["engine"], d["identity"]) as s:
        return UUID(
            str(
                s.execute(
                    text("SELECT id FROM generation_jobs ORDER BY created_at DESC")
                ).scalar_one()
            )
        )


def result(
    external: str, *, seconds: int = 5, amount: int = 5, proof: str = "f" * 64
) -> VideoTaskResult:
    return VideoTaskResult(
        external_id=external,
        duration_seconds=seconds,
        mime="video/mp4",
        size_bytes=1024,
        sha256="d" * 64,
        billing=BillingOutcome(
            state="KNOWN", amount_minor=amount, currency="CNY", proof_hash=proof
        ),
    )


async def test_video_submit_poll_success_and_duplicate_dispatch(
    video_ready: dict[str, Any],
) -> None:
    d = video_ready
    created(d)
    identifier = job_id(d)
    ack = d["video"].dispatch(identifier)
    assert ack["status"] == "WAITING_PROVIDER" and ack["submitState"] == "ACKNOWLEDGED"
    task = d["video"].get_task(identifier)
    assert task["state"] == "RUNNING" and task["externalTaskId"]
    assert task["pricingMode"] == "PER_SECOND" and task["requestedSeconds"] == 5
    assert ledger(d) == (5, 0, 0)
    assert d["video"].dispatch(identifier) == ack and len(d["videoProvider"].starts) == 1
    external = str(task["externalTaskId"])
    d["videoProvider"].poll_states = ["RUNNING", "SUCCEEDED"]
    running = d["video"].query(identifier)
    assert running["status"] == "WAITING_PROVIDER" and running["actualCostMinor"] is None
    assert d["video"].get_task(identifier)["pollCount"] == 1
    d["videoProvider"].result = result(external)
    done = d["video"].query(identifier)
    assert done["status"] == "WAITING_PROVIDER" and done["billingState"] == "KNOWN"
    assert done["actualCostMinor"] == 5 and ledger(d) == (0, 5, 0)
    settled = d["video"].get_task(identifier)
    assert settled["state"] == "SUCCEEDED"
    assert settled["billedSeconds"] == 5 and settled["resultMime"] == "video/mp4"
    assert settled["pollCount"] == 2
    assert d["video"].query(identifier) == done
    assert d["videoProvider"].polls == [external, external]
    assert d["videoProvider"].fetches == [external]
    # 本轮不落视频产物，因此不会伪造进入WAITING_REVIEW或资产采纳。
    assert done["resultAssetId"] is None


async def test_video_failure_keeps_unknown_cost(video_ready: dict[str, Any]) -> None:
    d = video_ready
    created(d)
    identifier = job_id(d)
    ack = d["video"].dispatch(identifier)
    external = str(d["video"].get_task(identifier)["externalTaskId"])
    d["videoProvider"].poll_states = ["FAILED"]
    failed = d["video"].query(identifier)
    assert failed["status"] == "FAILED" and failed["submitState"] == "ACKNOWLEDGED"
    assert failed["billingState"] == "UNKNOWN" and failed["actualCostMinor"] is None
    assert ledger(d) == (5, 0, 0)
    assert d["video"].get_task(identifier)["state"] == "FAILED"
    assert d["video"].query(identifier) == failed
    assert d["videoProvider"].polls == [external]
    assert ack["status"] == "WAITING_PROVIDER"


async def test_video_timeout_without_polling(video_ready: dict[str, Any]) -> None:
    d = video_ready
    created(d)
    identifier = job_id(d)
    d["video"].dispatch(identifier)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""UPDATE video_generation_tasks
            SET deadline_at=clock_timestamp()-interval '1 second',version=version+1
            WHERE job_id=:job"""),
            dict(job=identifier),
        )
    timed_out = d["video"].query(identifier)
    assert timed_out["status"] == "FAILED" and timed_out["billingState"] == "UNKNOWN"
    assert d["video"].get_task(identifier)["state"] == "TIMEOUT"
    assert d["videoProvider"].polls == []
    assert ledger(d) == (5, 0, 0)


async def test_video_cancel_stops_polling_without_refund_claim(
    video_ready: dict[str, Any],
) -> None:
    d = video_ready
    created(d)
    identifier = job_id(d)
    d["video"].dispatch(identifier)
    cancelled = d["video"].cancel(identifier)
    assert cancelled["status"] == "WAITING_PROVIDER"
    assert cancelled["billingState"] == "UNKNOWN"
    task = d["video"].get_task(identifier)
    assert task["state"] == "CANCELLED" and task["errorCode"] == "LOCAL_CANCELLED"
    assert d["video"].query(identifier) == cancelled
    assert d["videoProvider"].polls == [] and ledger(d) == (5, 0, 0)


async def test_video_unsent_cancel_releases_reservation(video_ready: dict[str, Any]) -> None:
    d = video_ready
    created(d)
    identifier = job_id(d)
    cancelled = d["video"].cancel(identifier)
    assert cancelled["status"] == "CANCELLED" and ledger(d) == (0, 0, 0)
    assert d["video"].cancel(identifier) == cancelled
    assert d["videoProvider"].starts == []


async def test_video_lost_start_response_unknown_not_resubmitted(
    video_ready: dict[str, Any],
) -> None:
    d = video_ready
    created(d)
    identifier = job_id(d)
    d["videoProvider"].start_error = TimeoutError("synthetic-video-response-lost")
    unknown = d["video"].dispatch(identifier)
    assert unknown["submitState"] == "UNKNOWN" and unknown["billingState"] == "UNKNOWN"
    assert d["video"].get_task(identifier)["state"] == "PENDING"
    assert ledger(d) == (5, 0, 0)
    assert d["video"].dispatch(identifier) == unknown
    assert len(d["videoProvider"].starts) == 0
    # 无外部标识时不能按请求键伪造轮询。
    assert d["video"].query(identifier) == unknown
    assert d["videoProvider"].polls == []


async def test_video_per_call_pricing(video_ready: dict[str, Any]) -> None:
    d = video_ready
    created(d, d["callInput"])
    identifier = job_id(d)
    d["video"].dispatch(identifier)
    task = d["video"].get_task(identifier)
    assert task["pricingMode"] == "PER_CALL" and task["requestedSeconds"] is None
    external = str(task["externalTaskId"])
    d["videoProvider"].poll_states = ["SUCCEEDED"]
    d["videoProvider"].result = result(external, seconds=5, amount=1)
    done = d["video"].query(identifier)
    assert done["actualCostMinor"] == 1 and done["billingState"] == "KNOWN"
    settled = d["video"].get_task(identifier)
    assert settled["state"] == "SUCCEEDED" and settled["billedSeconds"] is None
    assert ledger(d) == (0, 1, 0)


async def test_video_per_second_requires_expected_duration(
    video_ready: dict[str, Any],
) -> None:
    d = video_ready
    missing = d["videoInput"].model_copy(update=dict(durationSeconds=None))
    with pytest.raises(ApiError):
        d["video"].quote(missing)
    assert ledger(d) == (0, 0, 0)


async def test_video_unconfigured_provider_fails_before_send(
    video_ready: dict[str, Any],
) -> None:
    d = video_ready
    created(d)
    identifier = job_id(d)
    service = VideoGenerationService(d["docs"], UnconfiguredVideoProvider())
    with pytest.raises(ApiError):
        service.dispatch(identifier)
    with business_transaction(d["engine"], d["identity"]) as s:
        row = s.execute(
            text("SELECT status,submit_state FROM generation_jobs WHERE id=:job"),
            dict(job=identifier),
        ).one()
    assert tuple(row) == ("QUEUED", "NOT_SENT")
    assert ledger(d) == (5, 0, 0)
    assert d["videoProvider"].starts == []


async def test_video_task_ledger_rejects_forged_success(
    video_ready: dict[str, Any],
) -> None:
    d = video_ready
    created(d)
    identifier = job_id(d)
    d["video"].dispatch(identifier)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        with pytest.raises(DBAPIError):
            owner.execute(
                text("""UPDATE video_generation_tasks SET state='SUCCEEDED',
                result_mime='video/mp4',result_size_bytes=1024,result_sha256=:sha,
                result_duration_seconds=5,version=version+1 WHERE job_id=:job"""),
                dict(job=identifier, sha="d" * 64),
            )


async def test_video_task_terminal_state_cannot_revive(
    video_ready: dict[str, Any],
) -> None:
    d = video_ready
    created(d)
    identifier = job_id(d)
    d["video"].dispatch(identifier)
    d["videoProvider"].poll_states = ["FAILED"]
    d["video"].query(identifier)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        with pytest.raises(DBAPIError):
            owner.execute(
                text("""UPDATE video_generation_tasks SET state='RUNNING',
                version=version+1 WHERE job_id=:job"""),
                dict(job=identifier),
            )
