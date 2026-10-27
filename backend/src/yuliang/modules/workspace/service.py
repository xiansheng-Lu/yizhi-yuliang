"""本人项目/对话事务；幂等回放持久响应，归类不创建审批或执行任务。"""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.audit.service import Action, ObjectType, record
from yuliang.modules.workspace.models import MessageSend, ReportingScope
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

UNAVAILABLE = "模型能力尚未接入，当前无法可靠解析这条指令。输入已保存，请待能力验收后重试。"


class WorkspaceService:
    def __init__(self, engine: Engine, identity: VerifiedIdentity, request_id: UUID) -> None:
        self.engine = engine
        self.identity = identity
        self.request_id = request_id

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with business_transaction(self.engine, self.identity) as session:
            valid = session.execute(
                text("SELECT access.workspace_allowed(:member,:user)"),
                {"member": self.identity.membership_id, "user": self.identity.user_id},
            ).scalar_one()
            if not valid:
                raise ApiError(ErrorCode.FORBIDDEN, "没有中枢工作区权限")
            yield session

    def lock(self, session: Session, key: str) -> None:
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"workspace:{self.identity.tenant_id}:{self.identity.user_id}:{key}"},
        )

    def audit(
        self, session: Session, action: Action, kind: ObjectType, target: UUID, summary: object
    ) -> None:
        record(
            session,
            self.identity,
            {
                "action": action,
                "object_type": kind,
                "object_id": target,
                "request_id": self.request_id,
                "after_hash": hashlib.sha256(
                    json.dumps(summary, sort_keys=True).encode()
                ).hexdigest(),
            },
        )

    def project(self, session: Session, project_id: UUID | None) -> None:
        if (
            project_id is not None
            and session.execute(
                text("SELECT id FROM work_projects WHERE id=:id"), {"id": project_id}
            ).scalar_one_or_none()
            is None
        ):
            raise ApiError(ErrorCode.NO_DATA, "没有找到这个项目")

    def conversation(
        self, session: Session, conversation_id: UUID, *, lock: bool = False
    ) -> dict[str, Any]:
        statement = "SELECT * FROM conversations WHERE id=:id"
        if lock:
            statement += " FOR UPDATE"
        row = session.execute(text(statement), {"id": conversation_id}).mappings().first()
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "没有找到这条对话")
        messages = session.execute(
            text("SELECT * FROM conversation_messages WHERE conversation_id=:id ORDER BY position"),
            {"id": conversation_id},
        ).mappings()
        return {
            "id": str(row["id"]),
            "title": row["title"],
            "projectId": str(row["project_id"]) if row["project_id"] else None,
            "createdAt": row["created_at"].isoformat(),
            "updatedAt": row["updated_at"].isoformat(),
            "messages": [self.message(dict(message)) for message in messages],
        }

    @staticmethod
    def message(row: dict[str, Any]) -> dict[str, Any]:
        result = {
            "id": str(row["id"]),
            "role": row["role"],
            "text": row["text"],
            "createdAt": row["created_at"].isoformat(),
        }
        for field in ("interpretation", "scope"):
            if row[field] is not None:
                result[field] = row[field]
        if row["job_id"] is not None:
            result["jobId"] = str(row["job_id"])
        return result

    def history(self) -> dict[str, Any]:
        with self.transaction() as session:
            projects = [
                {"id": str(row.id), "name": row.name, "createdAt": row.created_at.isoformat()}
                for row in session.execute(
                    text("SELECT * FROM work_projects ORDER BY created_at DESC,id")
                )
            ]
            ids = (
                session.execute(text("SELECT id FROM conversations ORDER BY updated_at DESC,id"))
                .scalars()
                .all()
            )
            conversations = [self.conversation(session, key) for key in ids]
            assignments = {
                str(row.job_id): str(row.project_id) if row.project_id else None
                for row in session.execute(text("SELECT job_id,project_id FROM task_project_links"))
            }
            return {
                "projects": projects,
                "conversations": conversations,
                "taskProjects": assignments,
            }

    def create_project(self, name: str) -> dict[str, Any]:
        with self.transaction() as session:
            self.lock(session, "project-name:" + name)
            if session.execute(
                text("SELECT id FROM work_projects WHERE name=:name"), {"name": name}
            ).first():
                raise ApiError(ErrorCode.VERSION_CONFLICT, "已有同名项目")
            row = (
                session.execute(
                    text(
                        "INSERT INTO work_projects(tenant_id,user_id,name) "
                        "VALUES(:tenant,:user,:name) RETURNING *"
                    ),
                    {
                        "tenant": self.identity.tenant_id,
                        "user": self.identity.user_id,
                        "name": name,
                    },
                )
                .mappings()
                .one()
            )
            self.audit(session, "project.create", "project", row["id"], {"id": str(row["id"])})
            return {
                "id": str(row["id"]),
                "name": row["name"],
                "createdAt": row["created_at"].isoformat(),
            }

    def require_shop(self, session: Session, shop_id: UUID) -> None:
        allowed = session.execute(
            text("""
              SELECT EXISTS(SELECT 1 FROM shops shop
                JOIN data_scopes s ON s.tenant_id=shop.tenant_id
                WHERE shop.id=:shop AND shop.deleted_at IS NULL AND s.membership_id=:member
                AND (s.permissions_mask IS NULL OR 'task.create'=ANY(s.permissions_mask))
                AND (s.scope_type='ALL_TENANT' OR s.shop_id=shop.id OR s.entity_id=shop.entity_id
                  OR EXISTS(SELECT 1 FROM entity_links l
                    WHERE l.shop_id=shop.id AND l.account_id=s.account_id)))
            """),
            {"shop": shop_id, "member": self.identity.membership_id},
        ).scalar_one()
        if not allowed:
            raise ApiError(ErrorCode.FORBIDDEN, "没有该店铺的数据范围")

    def check_scope(self, session: Session, scope: ReportingScope) -> None:
        if scope.shop_id != "all":
            self.require_shop(session, scope.shop_id)
        if scope.live_session_id != "all":
            row = session.execute(
                text("SELECT shop_id FROM live_sessions WHERE id=:id"),
                {"id": scope.live_session_id},
            ).first()
            if row is None or (scope.shop_id != "all" and row.shop_id != scope.shop_id):
                raise ApiError(ErrorCode.FORBIDDEN, "场次不在当前范围")
            self.check_scope(
                session, scope.model_copy(update={"shop_id": row.shop_id, "live_session_id": "all"})
            )

    def send(self, value: MessageSend) -> dict[str, Any]:
        payload = value.model_dump(mode="json", exclude={"idempotencyKey"})
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        with self.transaction() as session:
            self.lock(session, "message-key:" + value.idempotencyKey)
            prior = session.execute(
                text("SELECT payload_hash,response FROM conversation_turns WHERE client_key=:key"),
                {"key": value.idempotencyKey},
            ).first()
            if prior:
                if prior.payload_hash != digest:
                    raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "发送标识已用于其他内容")
                return dict(prior.response)
            self.project(session, value.projectId)
            self.check_scope(session, value.scope)
            conversation_id = value.conversationId
            if conversation_id is None:
                conversation_id = uuid4()
                session.execute(
                    text("""INSERT INTO conversations(id,tenant_id,user_id,project_id,title)
                    VALUES(:id,:tenant,:user,:project,:title)"""),
                    {
                        "id": conversation_id,
                        "tenant": self.identity.tenant_id,
                        "user": self.identity.user_id,
                        "project": value.projectId,
                        "title": value.text[:36],
                    },
                )
            else:
                self.conversation(session, conversation_id, lock=True)
            interpretation = {"kind": "UNRECOGNIZED", "text": UNAVAILABLE}
            message_id = uuid4()
            for role, content, identifier in (
                ("user", value.text, uuid4()),
                ("assistant", UNAVAILABLE, message_id),
            ):
                session.execute(
                    text("""INSERT INTO conversation_messages(
                  id,tenant_id,user_id,conversation_id,role,text,interpretation,scope)
                  VALUES(:id,:tenant,:user,:conversation,:role,:text,
                    CAST(:interpretation AS jsonb),CAST(:scope AS jsonb))"""),
                    {
                        "id": identifier,
                        "tenant": self.identity.tenant_id,
                        "user": self.identity.user_id,
                        "conversation": conversation_id,
                        "role": role,
                        "text": content,
                        "interpretation": json.dumps(interpretation)
                        if role == "assistant"
                        else None,
                        "scope": value.scope.model_dump_json() if role == "assistant" else None,
                    },
                )
            session.execute(
                text("UPDATE conversations SET updated_at=now() WHERE id=:id"),
                {"id": conversation_id},
            )
            response = {
                "conversation": self.conversation(session, conversation_id),
                "interpretation": interpretation,
                "messageId": str(message_id),
            }
            session.execute(
                text("""INSERT INTO conversation_turns(
                  tenant_id,user_id,client_key,payload_hash,response)
              VALUES(:tenant,:user,:key,:hash,CAST(:response AS jsonb))"""),
                {
                    "tenant": self.identity.tenant_id,
                    "user": self.identity.user_id,
                    "key": value.idempotencyKey,
                    "hash": digest,
                    "response": json.dumps(response, ensure_ascii=False),
                },
            )
            self.audit(
                session,
                "message.send",
                "message",
                message_id,
                {"conversation": str(conversation_id)},
            )
            return response

    def move(self, conversation_id: UUID, project_id: UUID | None) -> dict[str, Any]:
        with self.transaction() as session:
            self.project(session, project_id)
            self.conversation(session, conversation_id, lock=True)
            session.execute(
                text("UPDATE conversations SET project_id=:project,updated_at=now() WHERE id=:id"),
                {"id": conversation_id, "project": project_id},
            )
            session.execute(
                text("""DELETE FROM task_project_links WHERE job_id IN
              (SELECT job_id FROM conversation_messages
               WHERE conversation_id=:id AND job_id IS NOT NULL)"""),
                {"id": conversation_id},
            )
            self.audit(
                session,
                "conversation.move",
                "conversation",
                conversation_id,
                {"project": str(project_id)},
            )
            return self.conversation(session, conversation_id)

    def job(self, session: Session, job_id: UUID) -> None:
        self.lock(session, "job:" + str(job_id))
        row = session.execute(
            text("SELECT shop_id,account_id FROM command_jobs WHERE id=:id"), {"id": job_id}
        ).first()
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "没有找到当前用户的任务")
        if row.account_id is not None:
            require_shop_account(session, self.identity, row.shop_id, row.account_id, "task.create")
        else:
            self.require_shop(session, row.shop_id)

    def attach(self, conversation_id: UUID, message_id: UUID, job_id: UUID) -> dict[str, Any]:
        with self.transaction() as session:
            self.conversation(session, conversation_id, lock=True)
            message = (
                session.execute(
                    text(
                        "SELECT * FROM conversation_messages WHERE id=:message "
                        "AND conversation_id=:conversation FOR UPDATE"
                    ),
                    {"message": message_id, "conversation": conversation_id},
                )
                .mappings()
                .first()
            )
            if (
                message is None
                or message["role"] != "assistant"
                or message["interpretation"].get("kind") != "ACTION"
            ):
                raise ApiError(ErrorCode.NO_DATA, "没有找到任务对应的动作消息")
            self.job(session, job_id)
            existing = session.execute(
                text("SELECT id FROM conversation_messages WHERE job_id=:job"), {"job": job_id}
            ).scalar_one_or_none()
            if message["job_id"] not in (None, job_id) or existing not in (None, message_id):
                raise ApiError(ErrorCode.VERSION_CONFLICT, "此消息或任务已有其他关联")
            session.execute(
                text("UPDATE conversation_messages SET job_id=:job WHERE id=:message"),
                {"job": job_id, "message": message_id},
            )
            session.execute(
                text("UPDATE conversations SET updated_at=now() WHERE id=:id"),
                {"id": conversation_id},
            )
            self.audit(session, "conversation.attach", "message", message_id, {"job": str(job_id)})
            return self.conversation(session, conversation_id)

    def assign(self, job_id: UUID, project_id: UUID | None) -> None:
        with self.transaction() as session:
            self.project(session, project_id)
            self.job(session, job_id)
            session.execute(
                text("""INSERT INTO task_project_links(tenant_id,user_id,job_id,project_id)
              VALUES(:tenant,:user,:job,:project) ON CONFLICT(tenant_id,user_id,job_id)
              DO UPDATE SET project_id=excluded.project_id,updated_at=now()"""),
                {
                    "tenant": self.identity.tenant_id,
                    "user": self.identity.user_id,
                    "job": job_id,
                    "project": project_id,
                },
            )
            self.audit(session, "task.project", "job", job_id, {"project": str(project_id)})
