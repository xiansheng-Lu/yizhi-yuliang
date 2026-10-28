"""真实模板与创作线事务；执行/采纳/重做归后续任务。"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from yuliang.modules.content.service import ContentService
from yuliang.modules.content.validation import validate_content
from yuliang.modules.content.workflows_models import RunCreate, TemplateDefinition
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class WorkflowService:
    def __init__(self, content: ContentService) -> None:
        self.content = content

    def _scope(self, session: Any, write: bool) -> None:
        if self.content.shop is None:
            raise ApiError(ErrorCode.INVALID_INPUT, "需要明确店铺范围")
        self.content._shop(session, self.content.shop, write)

    def templates(self) -> list[dict[str, Any]]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            self._scope(s, False)
            rows = [
                dict(row)
                for row in s.execute(
                    text(
                        "SELECT id,tenant_id,code,version,name,is_system,definition "
                        "FROM workflow_templates "
                        "ORDER BY code,version LIMIT 201"
                    )
                ).mappings()
            ]
            if len(rows) > 200:
                raise ApiError(
                    ErrorCode.INVALID_INPUT,
                    "模板目录超过200条读取上限；当前接口不支持分页，拒绝返回截断目录",
                )
            return rows

    def append_template(self, source: UUID, value: TemplateDefinition) -> dict[str, Any]:
        value = TemplateDefinition.model_validate(value.model_dump())
        tenant = self.content.identity.tenant_id
        with business_transaction(self.content.engine, self.content.identity) as s:
            self._scope(s, True)
            old = (
                s.execute(text("SELECT * FROM workflow_templates WHERE id=:id"), {"id": source})
                .mappings()
                .first()
            )
            if old is None:
                raise ApiError(ErrorCode.NO_DATA, "模板不存在")
            s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760164))"),
                {"key": f"{tenant}|template|{value.code}"},
            )
            version = s.execute(
                text(
                    "SELECT coalesce(max(version),0)+1 FROM workflow_templates "
                    "WHERE tenant_id=:tenant AND code=:code"
                ),
                {"tenant": tenant, "code": value.code},
            ).scalar_one()
            identifier = uuid4()
            s.execute(
                text(
                    "INSERT INTO workflow_templates(id,tenant_id,code,version,name,"
                    "is_system,definition) "
                    "VALUES(:id,:tenant,:code,:version,:name,false,CAST(:definition AS jsonb))"
                ),
                {
                    "id": identifier,
                    "tenant": tenant,
                    "code": value.code,
                    "version": version,
                    "name": value.name,
                    "definition": value.model_dump_json(),
                },
            )
            return {"id": str(identifier), "version": version, "definition": value.model_dump()}

    def create(self, value: RunCreate) -> dict[str, Any]:
        value = RunCreate.model_validate(value.model_dump())
        template, product = UUID(value.templateId), UUID(value.productId)
        tenant = self.content.identity.tenant_id
        digest = hashlib.sha256(
            json.dumps(
                value.model_dump() | {"shopId": str(self.content.shop)}, sort_keys=True
            ).encode()
        ).hexdigest()
        with business_transaction(self.content.engine, self.content.identity) as s:
            self._scope(s, True)
            self.content._product(s, str(product), True)
            s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760164))"),
                {"key": f"{tenant}|run|{value.creationKey}"},
            )
            old = (
                s.execute(
                    text("SELECT * FROM workflow_runs WHERE creation_key=:key"),
                    {"key": value.creationKey},
                )
                .mappings()
                .first()
            )
            if old:
                if old["request_hash"] != digest:
                    raise ApiError(ErrorCode.VERSION_CONFLICT, "创建键已用于其他创作线请求")
                return self._get(s, old["id"])
            row = (
                s.execute(text("SELECT * FROM workflow_templates WHERE id=:id"), {"id": template})
                .mappings()
                .first()
            )
            if row is None:
                raise ApiError(ErrorCode.NO_DATA, "模板不存在")
            definition = TemplateDefinition.model_validate(row["definition"])
            run, root = uuid4(), uuid4()
            document = validate_content(
                dict(
                    id=str(root),
                    kind="planning",
                    title=value.title,
                    productId=str(product),
                    parentId=None,
                    version=1,
                    updatedAt=datetime.now(UTC).isoformat(),
                    status="draft",
                    brief=dict(
                        audience="",
                        objective="",
                        sellingPoints="",
                        channel="视频号",
                        requirements="",
                    ),
                    body="",
                    scenes=[],
                    boards=[],
                    nodes=[],
                    edges=[],
                    clips=[],
                    prompt="",
                    settings=dict(ratio="9:16", duration=15, resolution="1080P", skill=""),
                ),
                purpose="SOP",
            )
            self.content._save(s, root, document)
            s.execute(
                text(
                    "INSERT INTO workflow_runs(id,tenant_id,shop_id,product_id,template_tenant_id,"
                    "template_id,template_version,root_document_id,creation_key,"
                    "request_hash,created_by) "
                    "VALUES(:id,:tenant,:shop,:product,:owner,:template,:version,:root,:key,:hash,:actor)"
                ),
                dict(
                    id=run,
                    tenant=tenant,
                    shop=self.content.shop,
                    product=product,
                    owner=row["tenant_id"],
                    template=template,
                    version=row["version"],
                    root=root,
                    key=value.creationKey,
                    hash=digest,
                    actor=self.content.identity.user_id,
                ),
            )
            s.execute(
                text("UPDATE content_documents SET workflow_run_id=:run WHERE id=:root"),
                dict(run=run, root=root),
            )
            self.content.reference_in_transaction(s, root, run, "RUN")
            for index, step in enumerate(definition.steps):
                s.execute(
                    text(
                        "INSERT INTO workflow_run_steps(id,tenant_id,run_id,step_code,"
                        "seq,status,definition) "
                        "VALUES(:id,:tenant,:run,:code,:seq,:status,CAST(:definition AS jsonb))"
                    ),
                    dict(
                        id=uuid4(),
                        tenant=tenant,
                        run=run,
                        code=step.code,
                        seq=index,
                        status="PENDING" if step.dependsOn else "READY",
                        definition=step.model_dump_json(),
                    ),
                )
            return self._get(s, run)

    def _get(self, s: Any, identifier: UUID) -> dict[str, Any]:
        row = (
            s.execute(text("SELECT * FROM workflow_runs WHERE id=:id"), {"id": identifier})
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "创作线不存在")
        self.content._shop(s, row["shop_id"], False)
        self.content._product(s, str(row["product_id"]), False)
        steps = s.execute(
            text(
                "SELECT id,step_code,seq,status,definition FROM workflow_run_steps "
                "WHERE run_id=:id ORDER BY seq"
            ),
            {"id": identifier},
        ).mappings()
        return dict(
            id=str(row["id"]),
            shopId=str(row["shop_id"]),
            productId=str(row["product_id"]),
            templateId=str(row["template_id"]),
            templateVersion=row["template_version"],
            rootDocumentId=str(row["root_document_id"]),
            status=row["status"],
            steps=[
                dict(
                    id=str(step["id"]),
                    code=step["step_code"],
                    status=step["status"],
                    definition=step["definition"],
                )
                for step in steps
            ],
        )

    def get(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            return self._get(s, identifier)

    def list_runs(self) -> list[dict[str, Any]]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            self._scope(s, False)
            ids = (
                s.execute(
                    text(
                        "SELECT id FROM workflow_runs WHERE shop_id=:shop "
                        "ORDER BY created_at DESC,id LIMIT 101"
                    ),
                    {"shop": self.content.shop},
                )
                .scalars()
                .all()
            )
            if len(ids) > 100:
                raise ApiError(
                    ErrorCode.INVALID_INPUT,
                    "当前店铺创作线超过100条读取上限；当前接口不支持分页，拒绝返回截断列表",
                )
            return [self._get(s, identifier) for identifier in ids]
