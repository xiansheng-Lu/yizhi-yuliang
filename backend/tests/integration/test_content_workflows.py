"""SOP模板、版本锁定、真实事务/HTTP与范围。"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.content.service import ContentService
from yuliang.modules.content.workflows_models import RunCreate, TemplateDefinition
from yuliang.modules.content.workflows_service import WorkflowService
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import challenge, sign_in
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products

pytestmark = pytest.mark.integration
SYSTEM = UUID("00000000-0000-4000-8000-000000000064")


def request(d: dict[str, Any], key: str = "create") -> RunCreate:
    return RunCreate(templateId=str(SYSTEM), productId=d["product"], creationKey=key)


def definition() -> TemplateDefinition:
    return TemplateDefinition.model_validate(
        dict(
            code="private",
            name="租户线",
            steps=[
                dict(code="brief", name="需求", kind="MANUAL"),
                dict(code="independent", name="独立", kind="MANUAL"),
                dict(code="script", name="脚本", kind="AI_ASSISTED", dependsOn=["brief"]),
            ],
        )
    )


@pytest.mark.asyncio
async def test_system_read_only_copy_and_locked_template_version(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    service = WorkflowService(d["content"])
    assert any(str(t["id"]) == str(SYSTEM) and t["is_system"] for t in service.templates())
    for sql in [
        "UPDATE workflow_templates SET name='changed' WHERE id=:id",
        "DELETE FROM workflow_templates WHERE id=:id",
    ]:
        with pytest.raises(DBAPIError), business_transaction(d["engine"], d["identity"]) as s:
            s.execute(text(sql), dict(id=SYSTEM))
    copied = service.append_template(SYSTEM, definition())
    value = request(d).model_copy(update={"templateId": copied["id"]})
    run = service.create(value)
    assert [step["status"] for step in run["steps"]] == ["READY", "READY", "PENDING"]
    assert service.append_template(UUID(copied["id"]), definition())["version"] == 2
    assert service.get(UUID(run["id"]))["templateVersion"] == 1
    with pytest.raises(ApiError):
        d["content"].delete(UUID(run["rootDocumentId"]), 1)
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(
            text("SELECT workflow_run_id FROM content_documents WHERE id=:id"),
            dict(id=UUID(run["rootDocumentId"])),
        ).scalar_one() == UUID(run["id"])


@pytest.mark.asyncio
async def test_concurrent_creation_key_and_payload_conflict(content_ready: dict[str, Any]) -> None:
    d = content_ready
    service = WorkflowService(d["content"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: service.create(request(d)), range(2)))
    assert runs[0]["id"] == runs[1]["id"]
    with pytest.raises(ApiError):
        service.create(request(d).model_copy(update={"title": "其他请求"}))
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM workflow_runs")).scalar_one() == 1
        assert s.execute(text("SELECT count(*) FROM content_documents")).scalar_one() == 1


@pytest.mark.asyncio
async def test_create_failure_rolls_back_root_run_and_steps(
    content_ready: dict[str, Any], monkeypatch: Any
) -> None:
    d = content_ready

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected reference failure")

    monkeypatch.setattr(d["content"], "reference_in_transaction", fail)
    with pytest.raises(RuntimeError):
        WorkflowService(d["content"]).create(request(d))
    with business_transaction(d["engine"], d["identity"]) as s:
        for table in [
            "workflow_runs",
            "workflow_run_steps",
            "content_documents",
            "content_document_versions",
        ]:
            query = "SELECT count(*) FROM " + table  # noqa: S608
            assert s.execute(text(query)).scalar_one() == 0


@pytest.mark.asyncio
async def test_cross_tenant_shop_and_removed_product_rejected(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    service = WorkflowService(d["content"])
    run = service.create(request(d))
    wrong = ContentService(d["engine"], d["identity"], uuid4())
    with pytest.raises(ApiError):
        WorkflowService(wrong).get(UUID(run["id"]))
    with pytest.raises(ApiError):
        service.create(request(d, "bad").model_copy(update={"productId": str(uuid4())}))
    outsider = ContentService(
        d["engine"],
        VerifiedIdentity(d["tenant"] + 99999, d["identity"].membership_id, d["identity"].user_id),
        d["shop"],
    )
    with pytest.raises(ApiError):
        WorkflowService(outsider).get(UUID(run["id"]))
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        s.execute(
            text("UPDATE commerce_products SET deleted_at=clock_timestamp() WHERE id=:id"),
            dict(id=UUID(d["product"])),
        )
    with pytest.raises(ApiError):
        service.create(request(d, "removed"))


@pytest.mark.asyncio
async def test_real_http_cookie_csrf_create_get_and_sop_validation(
    content_ready: dict[str, Any], monkeypatch: Any
) -> None:
    from yuliang.modules.content.validation import validate_content as original

    d = content_ready
    client = d["client"]
    client.cookies.clear()
    sign_in(d)
    headers = {"X-CSRF-Token": challenge(client), "X-Shop-Id": str(d["shop"])}
    purposes = []

    def validate(*args: Any, **kwargs: Any) -> Any:
        purposes.append(kwargs.get("purpose"))
        return original(*args, **kwargs)

    monkeypatch.setattr("yuliang.modules.content.workflows_service.validate_content", validate)
    assert (
        client.post(
            "/v1/content/workflow-runs",
            json=request(d).model_dump(),
            headers={"X-Shop-Id": str(d["shop"])},
        ).status_code
        == 403
    )
    result = client.post("/v1/content/workflow-runs", json=request(d).model_dump(), headers=headers)
    assert result.status_code == 200, result.text
    run = result.json()["data"]
    assert purposes == ["SOP"]
    assert (
        client.get("/v1/content/workflow-runs/" + run["id"], headers=headers).json()["data"]["id"]
        == run["id"]
    )
    assert client.get("/v1/content/workflow-templates", headers=headers).status_code == 200
    assert (
        client.post(
            "/v1/content/workflow-runs/" + run["id"] + "/execute", json={}, headers=headers
        ).status_code
        == 404
    )


@pytest.mark.asyncio
async def test_template_sql_system_impersonation_and_version_fk(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    with pytest.raises(DBAPIError), business_transaction(d["engine"], d["identity"]) as s:
        s.execute(
            text(
                "INSERT INTO workflow_templates(id,tenant_id,code,version,name,"
                "is_system,definition) "
                "VALUES(:id,0,'forged',1,'伪系统',true,'{}')"
            ),
            dict(id=uuid4()),
        )
    service = WorkflowService(d["content"])
    run = service.create(request(d))
    with pytest.raises(DBAPIError), business_transaction(d["engine"], d["identity"]) as s:
        s.execute(
            text(
                "INSERT INTO workflow_runs(id,tenant_id,shop_id,product_id,template_tenant_id,"
                "template_id,template_version,root_document_id,creation_key,"
                "request_hash,created_by) "
                "SELECT :id,tenant_id,shop_id,product_id,template_tenant_id,template_id,999,"
                "root_document_id,'forged-version','hash',created_by "
                "FROM workflow_runs WHERE id=:old"
            ),
            dict(id=uuid4(), old=UUID(run["id"])),
        )
    bad = definition().model_copy(update={"steps": [definition().steps[0], definition().steps[0]]})
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        service.append_template(SYSTEM, bad)


async def test_template_directory_exact_limit_latest_version_and_explicit_overflow(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    service = WorkflowService(d["content"])
    system_count = len(service.templates())
    value = definition().model_copy(update=dict(code="aaa_limit_test"))
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO workflow_templates(id,tenant_id,code,version,name,
                is_system,definition)
            SELECT gen_random_uuid(),:tenant,:code,v,:name,false,CAST(:definition AS jsonb)
            FROM generate_series(1,:count) AS v"""),
            dict(
                tenant=d["tenant"],
                code=value.code,
                name=value.name,
                definition=value.model_dump_json(),
                count=200 - system_count,
            ),
        )
    rows = service.templates()
    assert len(rows) == 200
    versions = [r for r in rows if r["code"] == value.code]
    assert max(r["version"] for r in versions) == 200 - system_count
    latest = max(versions, key=lambda r: r["version"])
    new = service.append_template(latest["id"], value)
    assert new["version"] == 201 - system_count
    with pytest.raises(ApiError) as failure:
        service.templates()
    assert failure.value.code.value == "INVALID_INPUT" and "200" in failure.value.message
    client = d["client"]
    client.cookies.clear()
    sign_in(d)
    response = client.get("/v1/content/workflow-templates", headers={"X-Shop-Id": str(d["shop"])})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


async def test_run_directory_exact_limit_and_explicit_overflow(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    service = WorkflowService(d["content"])
    first = service.create(request(d, "first"))
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""CREATE TEMP TABLE workflow_limit_seeds ON COMMIT DROP AS
            SELECT gen_random_uuid() AS root,gen_random_uuid() AS run,i
            FROM generate_series(1,99) AS i""")
        )
        owner.execute(
            text("""INSERT INTO content_documents(id,tenant_id,shop_id,product_id,
            parent_id,kind,revision,payload)
            SELECT seed.root,d.tenant_id,d.shop_id,d.product_id,NULL,d.kind,1,
            jsonb_set(d.payload,'{id}',to_jsonb(seed.root::text))
            FROM workflow_limit_seeds seed CROSS JOIN content_documents d WHERE d.id=:root"""),
            dict(root=UUID(first["rootDocumentId"])),
        )
        owner.execute(
            text("""INSERT INTO content_document_versions(tenant_id,document_id,revision,payload)
            SELECT d.tenant_id,d.id,1,d.payload FROM content_documents d
            JOIN workflow_limit_seeds seed ON seed.root=d.id""")
        )
        owner.execute(
            text("""INSERT INTO workflow_runs(id,tenant_id,shop_id,product_id,
            template_tenant_id,template_id,template_version,root_document_id,creation_key,
            request_hash,created_by)
            SELECT seed.run,r.tenant_id,r.shop_id,r.product_id,r.template_tenant_id,r.template_id,
            r.template_version,seed.root,'limit-'||seed.i,'synthetic-limit-only',r.created_by
            FROM workflow_limit_seeds seed CROSS JOIN workflow_runs r WHERE r.id=:run"""),
            dict(run=UUID(first["id"])),
        )
        owner.execute(
            text("""UPDATE content_documents d SET workflow_run_id=seed.run
            FROM workflow_limit_seeds seed WHERE d.id=seed.root""")
        )
        owner.execute(
            text("""INSERT INTO workflow_run_steps(id,tenant_id,run_id,step_code,
                seq,status,definition)
            SELECT gen_random_uuid(),r.tenant_id,seed.run,r.step_code,r.seq,r.status,r.definition
            FROM workflow_limit_seeds seed CROSS JOIN workflow_run_steps r WHERE r.run_id=:run"""),
            dict(run=UUID(first["id"])),
        )
        owner.execute(
            text("""INSERT INTO content_document_references(tenant_id,document_id,kind,reference_id)
            SELECT :tenant,seed.root,'RUN',seed.run FROM workflow_limit_seeds seed"""),
            dict(tenant=d["tenant"]),
        )
    rows = service.list_runs()
    assert len(rows) == 100 and len({r["id"] for r in rows}) == 100
    service.create(request(d, "overflow"))
    with pytest.raises(ApiError) as failure:
        service.list_runs()
    assert failure.value.code.value == "INVALID_INPUT" and "100" in failure.value.message
    client = d["client"]
    client.cookies.clear()
    sign_in(d)
    response = client.get("/v1/content/workflow-runs", headers={"X-Shop-Id": str(d["shop"])})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
