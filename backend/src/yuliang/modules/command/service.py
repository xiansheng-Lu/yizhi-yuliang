"""解析留痕；目录仅从受控服务端端口读，不创建任务/审批/执行。"""

import hashlib
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.aikernel.deepseek import PreparedCall
from yuliang.modules.aikernel.prompts import load_bundle
from yuliang.modules.aikernel.prompts.registry import CandidateObject, CaseContext, Interpretation
from yuliang.modules.command.models import ParseInput
from yuliang.modules.command.parser import RULE_VERSION, guard_model, parse_rules, wire
from yuliang.modules.workspace.models import ReportingScope
from yuliang.platform.errors import ApiError, ErrorCode

if TYPE_CHECKING:
    from yuliang.modules.workspace.service import WorkspaceService

type Catalog = Callable[[Session, ReportingScope], CaseContext]


class CommandParser:
    def __init__(self, workspace: "WorkspaceService", catalog: Catalog | None = None) -> None:
        self.workspace = workspace
        self.catalog = catalog or self.live_catalog

    def live_catalog(self, session: Session, scope: ReportingScope) -> CaseContext:
        candidates: list[CandidateObject] = []
        rows = session.execute(
            text("""SELECT live.id,live.shop_id,live.title FROM live_sessions live
              WHERE (:all_shops OR live.shop_id=CAST(:shop AS uuid))
              AND (:all_lives OR live.id=CAST(:live AS uuid))
              AND EXISTS(SELECT 1 FROM shops shop JOIN data_scopes s
                ON s.tenant_id=shop.tenant_id
                WHERE shop.id=live.shop_id AND shop.deleted_at IS NULL
                AND s.membership_id=:member
                AND (s.permissions_mask IS NULL OR 'task.create'=ANY(s.permissions_mask))
                AND (s.scope_type='ALL_TENANT' OR s.shop_id=shop.id OR s.entity_id=shop.entity_id
                  OR EXISTS(SELECT 1 FROM entity_links l
                    WHERE l.shop_id=shop.id AND l.account_id=s.account_id)))
              ORDER BY live.id LIMIT 21"""),
            {
                "all_shops": scope.shop_id == "all",
                "shop": None if scope.shop_id == "all" else scope.shop_id,
                "all_lives": scope.live_session_id == "all",
                "live": None if scope.live_session_id == "all" else scope.live_session_id,
                "member": self.workspace.identity.membership_id,
            },
        )
        for row in rows:
            try:
                self.workspace.require_shop(session, row.shop_id)
            except ApiError as error:
                if error.code != ErrorCode.FORBIDDEN:
                    raise
                continue
            candidates.append(
                CandidateObject(
                    id=row.id, target_type="LIVE_SESSION", name=row.title or "未命名场次"
                )
            )
        # 目录过多不截断后选择，要求缩小到具体店铺/场次。
        if len(candidates) > 20 or scope.connector_id != "all":
            candidates = []
        return CaseContext(
            candidates=tuple(candidates),
            allowed_actions=("REQUEST_LIVE_START", "REQUEST_LIVE_END"),
        )

    def interpret(
        self,
        session: Session,
        value: ParseInput,
        proposed: Interpretation | None = None,
        call_id: UUID | None = None,
        prepared: PreparedCall | None = None,
    ) -> dict[str, Any]:
        if (proposed is None) != (call_id is None):
            raise ValueError("模型候选必须关联真实调用账本，规则解析不能伪造调用ID")
        self.workspace.check_scope(session, value.scope)
        bundle = load_bundle()
        bundle.require_human_labels()
        context = self.catalog(session, value.scope)
        source = "RULES"
        if proposed is not None:
            if prepared is None:
                raise ValueError("模型解析必须携带冻结的已报价请求")
            body = json.loads(prepared.body)
            bound = json.loads(body["messages"][1]["content"])
            if (
                prepared.request.call_id != call_id
                or prepared.request.request_id != self.workspace.request_id
                or prepared.request.scenario != "command_parse"
                or prepared.request.capability_alias != "command.parse"
                or prepared.request.prompt_key != "command_parse"
                or prepared.request.prompt_version != bundle.manifest.prompt_version
                or prepared.billable.scenario != "command_parse"
                or prepared.billable.kind != "LLM"
                or prepared.billable.request_key != call_id
                or prepared.quote.request_key != call_id
                or prepared.context != context
                or bound != {"statement": value.text, "context": context.model_dump(mode="json")}
                or hashlib.sha256(prepared.body.encode()).hexdigest()
                != prepared.billable.payload_hash
            ):
                raise ApiError(ErrorCode.FORBIDDEN, "模型调用与当前输入或授权上下文不符")
            invocation = session.execute(
                text("""SELECT a.status FROM ai_invocations a JOIN ai_cost_quotes q
                  ON q.tenant_id=a.tenant_id AND q.request_key=a.call_id
                  WHERE a.call_id=:call AND a.user_id=:user AND a.membership_id=:member
                  AND a.request_id=:request AND a.scenario='command_parse'
                  AND a.prompt_version=:version
                  AND a.model_config_snapshot->>'prompt_hash'=:prompt
                  AND a.model_config_snapshot=CAST(:route AS jsonb)
                  AND a.status<>'STARTED' AND q.id=:quote AND q.actor_id=:user
                  AND q.request_fingerprint=:fingerprint"""),
                {
                    "call": call_id,
                    "user": self.workspace.identity.user_id,
                    "member": self.workspace.identity.membership_id,
                    "request": self.workspace.request_id,
                    "version": bundle.manifest.prompt_version,
                    "prompt": bundle.manifest.template_sha256,
                    "route": prepared.route.model_dump_json(),
                    "quote": prepared.quote.quote_id,
                    "fingerprint": prepared.billable.fingerprint(),
                },
            ).first()
            if invocation is None:
                raise ApiError(ErrorCode.FORBIDDEN, "缺少匹配的已完成模型调用与报价事实")
            source = "MODEL_GUARDED" if invocation.status == "OK" else "MODEL_UNAVAILABLE"
            if source == "MODEL_UNAVAILABLE" and proposed.kind != "UNRECOGNIZED":
                raise ApiError(ErrorCode.FORBIDDEN, "不可用的模型调用不能给出动作或查询候选")
        result = (
            parse_rules(value.text, context)
            if proposed is None
            else guard_model(value.text, context, proposed)
        )
        response = wire(result)
        # 原话已在私有消息中保存；解析表只存不可逆摘要，原始模型正文不入库。
        session.execute(
            text("""INSERT INTO interpretations(tenant_id,user_id,request_id,input_hash,scope,
              result,source,rules_version,prompt_version,prompt_hash,call_id)
              VALUES(:tenant,:user,:request,:input,CAST(:scope AS jsonb),CAST(:result AS jsonb),
                :source,:rules,:version,:prompt,:call)"""),
            {
                "tenant": self.workspace.identity.tenant_id,
                "user": self.workspace.identity.user_id,
                "request": self.workspace.request_id,
                "input": hashlib.sha256(value.text.encode()).hexdigest(),
                "scope": value.scope.model_dump_json(),
                "result": json.dumps(response, ensure_ascii=False),
                "source": source,
                "rules": RULE_VERSION,
                "version": bundle.manifest.prompt_version,
                "prompt": bundle.manifest.template_sha256,
                "call": call_id,
            },
        )
        return response
