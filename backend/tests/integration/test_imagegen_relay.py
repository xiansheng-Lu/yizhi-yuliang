"""中转站生图闭环：真实PG/MinIO + 假HTTP传输，不发生真实付费调用。"""

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.content.assets import AssetService
from yuliang.modules.content.generation_models import GenerationCreate, GenerationInput
from yuliang.modules.content.generation_service import GenerationService
from yuliang.modules.content.relay_image_generation_provider import (
    RelayImageGenerationProvider,
    UploadBackedImageWriter,
)
from yuliang.modules.content.workflows_models import RunCreate, TemplateDefinition
from yuliang.modules.content.workflows_service import WorkflowService
from yuliang.platform.db import business_transaction

from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_configuration_context import configuration_ready as configuration_ready
from .test_content_assets import assets_ready as assets_ready
from .test_content_assets import graphic
from .test_content_uploads import blob as blob
from .test_content_uploads import png as tiny_png
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products

pytestmark = pytest.mark.integration

ENDPOINT = "https://relay.test.invalid/v1/api/generate"
RESULT_URL = "https://file.test.invalid/output.png"
EXTERNAL_ID = "2-5486c9a3-0000-4000-8000-000000000002"
PRICE_MINOR = 37


class RelayTransport:
    def __init__(self, image: bytes) -> None:
        self.image = image
        self.posts: list[dict[str, Any]] = []
        self.downloads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(ENDPOINT):
            self.posts.append(dict(json.loads(request.content)))
            return httpx.Response(
                200,
                json={
                    "id": EXTERNAL_ID,
                    "status": "succeeded",
                    "results": [{"url": RESULT_URL}],
                    "progress": 100,
                },
            )
        if request.url == httpx.URL(RESULT_URL):
            self.downloads += 1
            return httpx.Response(
                200, content=self.image, headers={"content-type": "image/png"}
            )
        raise AssertionError("未预期的下游地址")


@pytest.fixture
async def imagegen_ready(assets_ready: dict[str, Any]) -> dict[str, Any]:
    d = assets_ready
    workflow = WorkflowService(d["docs"])
    template = workflow.append_template(
        UUID("00000000-0000-4000-8000-000000000064"),
        TemplateDefinition.model_validate(
            dict(
                code="synthetic_image_generation",
                name="中转站图像生成",
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
            valid_until,verification,proof_hash)
            VALUES(:tenant,'relay.image.job',1,'GENERATION','CNY','JOB',1,:rate,
            0,now()-interval '1 day',now()+interval '1 day','VERIFIED',:proof)
            RETURNING id"""),
            dict(tenant=d["tenant"], rate=PRICE_MINOR * 1000000, proof="d" * 64),
        ).scalar_one()
        owner.execute(
            text("""INSERT INTO generation_price_sources(id,tenant_id,provider,kind,
            price_id,unit_semantic,proof_ref,source_sha256,checked_at,expires_at)
            VALUES(:id,:tenant,'relay-image','IMAGE',:price,'ONE_REQUEST_JOB',
            'synthetic-imagegen-only-price-unit',:sha,
            now()-interval '1 minute',now()+interval '1 day')"""),
            dict(id=uuid4(), tenant=d["tenant"], price=price, sha="d" * 64),
        )
    value = GenerationInput.model_validate(
        dict(
            requestKey=str(uuid4()),
            runId=run["id"],
            stepCode="generate",
            expectedRunVersion=1,
            expectedAttempt=1,
            documentId=run["rootDocumentId"],
            documentVersion=saved["version"],
            kind="IMAGE",
            provider="relay-image",
            priceKey="relay.image.job",
            prompt="合成图像技术验证，经假传输返回一张极小PNG",
        )
    )
    image = tiny_png()
    transport = RelayTransport(image)
    provider = RelayImageGenerationProvider(
        endpoint=ENDPOINT,
        model="gpt-image-2.5",
        api_key=SecretStr("synthetic-not-real-key"),
        price_minor=PRICE_MINOR,
        writer=UploadBackedImageWriter(d["docs"]),
        transport=httpx.MockTransport(transport),
    )
    service = GenerationService(d["docs"], provider)
    return d | dict(
        imagegen=service,
        imageInput=value,
        imageProvider=provider,
        imageTransport=transport,
        imageBytes=image,
    )


def accepted(d: dict[str, Any], value: GenerationInput) -> GenerationCreate:
    quote = d["imagegen"].quote(value)
    return GenerationCreate(
        input=value,
        quoteId=quote["quote_id"],
        quoteHash=quote["quote_hash"],
        maximumMinor=quote["max_cost_minor"],
        currency=quote["currency"],
    )


def ledger(d: dict[str, Any]) -> tuple[int, int]:
    with business_transaction(d["engine"], d["identity"]) as s:
        row = s.execute(
            text(
                "SELECT reserved_minor,settled_minor FROM ai_cost_budgets "
                "WHERE scenario='content_generation'"
            )
        ).one()
    return int(row[0]), int(row[1])


def test_generated_png_becomes_confirmed_asset_with_known_price(
    imagegen_ready: dict[str, Any],
) -> None:
    d = imagegen_ready
    create = accepted(d, d["imageInput"])
    d["imagegen"].confirm(create)
    job = d["imagegen"].create(create)
    identifier = UUID(job["id"])
    result = d["imagegen"].dispatch(identifier)
    assert result["status"] == "WAITING_REVIEW"
    assert result["submitState"] == "ACKNOWLEDGED"
    assert result["externalJobId"] == EXTERNAL_ID
    assert result["actualCostMinor"] == PRICE_MINOR
    assert result["billingState"] == "KNOWN"
    asset_id = UUID(result["resultAssetId"])
    asset = AssetService(d["engine"], d["identity"], d["shop"], d["blob"]).get(asset_id)
    assert asset["kind"] == "image"
    assert asset["mime"] == "image/png"
    assert asset["size"] == len(d["imageBytes"])
    assert asset["productId"] == str(d["product"])
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT sha256 FROM content_assets WHERE id=:id"), dict(id=asset_id)
            ).scalar_one()
            == hashlib.sha256(d["imageBytes"]).hexdigest()
        )
    assert d["imageTransport"].downloads == 1
    assert ledger(d) == (0, PRICE_MINOR)
    with business_transaction(d["engine"], d["identity"]) as s:
        permit = s.execute(
            text("""SELECT state,settled_cost_minor FROM ai_cost_permits
            WHERE id=(SELECT permit_id FROM generation_jobs WHERE id=:id)"""),
            dict(id=identifier),
        ).one()
    assert tuple(permit) == ("SETTLED", PRICE_MINOR)
    # 同一任务再次派发命中SENT栅栏：不重复付费调用，也不产生第二份资产。
    again = d["imagegen"].dispatch(identifier)
    assert again["resultAssetId"] == str(asset_id)
    assert len(d["imageTransport"].posts) == 1
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_assets WHERE id=:id"), dict(id=asset_id)
            ).scalar_one()
            == 1
        )


def test_provider_failed_receipt_marks_job_failed_without_asset(
    imagegen_ready: dict[str, Any],
) -> None:
    d = imagegen_ready

    def failed_transport(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(ENDPOINT):
            return httpx.Response(
                200,
                json={"id": EXTERNAL_ID, "status": "failed", "error": "prompt is empty"},
            )
        raise AssertionError("失败回执不应继续下载")

    provider = RelayImageGenerationProvider(
        endpoint=ENDPOINT,
        model="gpt-image-2.5",
        api_key=SecretStr("synthetic-not-real-key"),
        price_minor=PRICE_MINOR,
        writer=UploadBackedImageWriter(d["docs"]),
        transport=httpx.MockTransport(failed_transport),
    )
    service = GenerationService(d["docs"], provider)
    create = accepted(d, d["imageInput"])
    d["imagegen"].confirm(create)
    job = d["imagegen"].create(create)
    result = service.dispatch(UUID(job["id"]))
    assert result["status"] == "FAILED"
    assert result["resultAssetId"] is None
    assert result["billingState"] == "UNKNOWN"
    assert result["actualCostMinor"] is None
    assert ledger(d)[1] == 0
