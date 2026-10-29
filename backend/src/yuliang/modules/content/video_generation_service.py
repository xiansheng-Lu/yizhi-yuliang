"""视频生成编排骨架：复用生成账本与费用栅栏，外部只经异步三段式适配器调用。

本轮不接真实供应商，也不落视频产物；供应商成功只推进本地任务账本与费用事实，
视频文件入库/采纳仍由后续任务承接。
"""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from yuliang.modules.content.generation_provider import ProviderResult
from yuliang.modules.content.generation_service import GenerationService
from yuliang.modules.content.service import ContentService
from yuliang.modules.content.video_generation_models import (
    VideoStartRequest,
    VideoTaskPoll,
    VideoTaskResult,
    can_transition,
)
from yuliang.modules.content.video_generation_provider import (
    UnconfiguredVideoProvider,
    VideoGenerationProvider,
)
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

TERMINAL = frozenset({"SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED"})


class VideoGenerationService(GenerationService):
    def __init__(
        self,
        content: ContentService,
        provider: VideoGenerationProvider,
        *,
        timeout_seconds: int = 900,
    ) -> None:
        super().__init__(content)
        if type(timeout_seconds) is not int or not 60 <= timeout_seconds <= 86400:
            raise ValueError("视频任务超时须为60至86400秒")
        self.video = provider
        self.timeout = timeout_seconds

    def _ensure_dispatchable(self) -> None:
        if isinstance(self.video, UnconfiguredVideoProvider):
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "视频生成供应商未配置，未发送"
            )

    def _before_send(self, s: Any, row: Any, identifier: UUID) -> None:
        if row["kind"] != "VIDEO":
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "视频编排只处理VIDEO生成")
        pricing = (
            s.execute(
                text("""SELECT price_snapshot->>'unit' AS unit,
                (price_snapshot->>'input_units')::bigint AS units FROM ai_cost_quotes
                WHERE tenant_id=:tenant AND id=:quote"""),
                dict(
                    tenant=self.content.identity.tenant_id,
                    quote=row["quote_id"],
                ),
            )
            .mappings()
            .one()
        )
        per_second = pricing["unit"] == "SECOND"
        seconds = int(pricing["units"]) if per_second else row["parameters"].get(
            "durationSeconds"
        )
        s.execute(
            text("""INSERT INTO video_generation_tasks(id,tenant_id,job_id,provider,
            state,attempt_no,pricing_mode,requested_seconds,deadline_at)
            VALUES(:id,:tenant,:job,:provider,'PENDING',1,:mode,:seconds,
            clock_timestamp()+(:timeout*interval '1 second'))"""),
            dict(
                id=uuid4(),
                tenant=self.content.identity.tenant_id,
                job=identifier,
                provider=row["provider"],
                mode="PER_SECOND" if per_second else "PER_CALL",
                seconds=seconds,
                timeout=self.timeout,
            ),
        )

    def _send(self, identifier: UUID, frozen: dict[str, Any]) -> ProviderResult:
        parameters = frozen["parameters"]
        request = VideoStartRequest.model_validate(
            dict(
                prompt=str(parameters.get("prompt", "")),
                duration_seconds=parameters.get("durationSeconds"),
            )
        )
        try:
            started = self.video.start_task(str(frozen["request_key"]), request)
        except Exception:
            # SENT已在独立事务提交；启动回执丢失只能保留UNKNOWN，不能重复提交。
            return ProviderResult("UNKNOWN")
        self._started(identifier, started.external_id)
        return ProviderResult("ACKNOWLEDGED", external_id=started.external_id)

    def _task(self, s: Any, identifier: UUID, *, lock: bool = False) -> Any:
        query = (
            "SELECT * FROM video_generation_tasks WHERE tenant_id=:tenant "
            "AND job_id=:job FOR UPDATE"
            if lock
            else "SELECT * FROM video_generation_tasks WHERE tenant_id=:tenant AND job_id=:job"
        )
        row = (
            s.execute(
                text(query),
                dict(tenant=self.content.identity.tenant_id, job=identifier),
            )
            .mappings()
            .first()
        )
        return row

    def _started(self, identifier: UUID, external: str) -> None:
        with business_transaction(self.content.engine, self.content.identity) as s:
            task = self._task(s, identifier, lock=True)
            if task is None:
                raise ApiError(ErrorCode.RESULT_UNKNOWN, "视频任务账本缺失，不能登记外部任务")
            if task["external_task_id"] is not None:
                if task["external_task_id"] != external:
                    raise ApiError(ErrorCode.RESULT_UNKNOWN, "同请求键返回冲突外部任务标识")
                return
            if not can_transition(task["state"], "RUNNING"):
                raise ApiError(ErrorCode.RESULT_UNKNOWN, "视频任务状态不允许进入RUNNING")
            s.execute(
                text("""UPDATE video_generation_tasks SET state='RUNNING',
                external_task_id=:external,version=version+1,updated_at=clock_timestamp()
                WHERE tenant_id=:tenant AND job_id=:job"""),
                dict(
                    tenant=self.content.identity.tenant_id,
                    job=identifier,
                    external=external,
                ),
            )

    def _store(
        self, identifier: UUID, poll: VideoTaskPoll, result: VideoTaskResult | None
    ) -> None:
        with business_transaction(self.content.engine, self.content.identity) as s:
            task = self._task(s, identifier, lock=True)
            if task is None or task["state"] in TERMINAL:
                return
            if not can_transition(task["state"], poll.state):
                raise ApiError(ErrorCode.RESULT_UNKNOWN, "供应商视频任务状态回退")
            billed = (
                result.duration_seconds
                if result is not None and task["pricing_mode"] == "PER_SECOND"
                else None
            )
            s.execute(
                text("""UPDATE video_generation_tasks SET state=:state,external_task_id=:external,
                poll_count=poll_count+1,billed_seconds=:billed,result_mime=:mime,
                result_size_bytes=:size,result_sha256=:sha,
                result_duration_seconds=:duration,version=version+1,
                updated_at=clock_timestamp() WHERE tenant_id=:tenant AND job_id=:job"""),
                dict(
                    tenant=self.content.identity.tenant_id,
                    job=identifier,
                    state=poll.state,
                    external=poll.external_id,
                    billed=billed,
                    mime=result.mime if result else None,
                    size=result.size_bytes if result else None,
                    sha=result.sha256 if result else None,
                    duration=result.duration_seconds if result else None,
                ),
            )

    def _transition(self, identifier: UUID, target: str, error_code: str) -> None:
        with business_transaction(self.content.engine, self.content.identity) as s:
            task = self._task(s, identifier, lock=True)
            if task is None or task["state"] in TERMINAL:
                return
            if not can_transition(task["state"], target):
                raise ApiError(ErrorCode.RESULT_UNKNOWN, "视频任务状态不允许此迁移")
            s.execute(
                text("""UPDATE video_generation_tasks SET state=:state,error_code=:error,
                version=version+1,updated_at=clock_timestamp()
                WHERE tenant_id=:tenant AND job_id=:job"""),
                dict(
                    tenant=self.content.identity.tenant_id,
                    job=identifier,
                    state=target,
                    error=error_code,
                ),
            )

    def query(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            row = self.repo.job(s, identifier)
            task = self._task(s, identifier)
            if row["submit_state"] == "NOT_SENT" or row["status"] == "CANCELLED" or task is None:
                return self.repo.view(row)
            if task["state"] in TERMINAL:
                return self.repo.view(row)
            if row["status"] in {"SUCCEEDED", "FAILED", "WAITING_REVIEW"} and (
                row["billing_state"] == "KNOWN"
            ):
                return self.repo.view(row)
            timed_out = task["deadline_at"] <= s.execute(
                text("SELECT clock_timestamp()")
            ).scalar_one()
            external = task["external_task_id"]
        if timed_out:
            self._transition(identifier, "TIMEOUT", "PROVIDER_TIMEOUT")
            return self._record(identifier, ProviderResult("FAILED", external_id=external))
        if external is None:
            # 启动回执丢失时没有可轮询的外部标识；不按请求键伪造查询，费用保持UNKNOWN。
            return self.repo.view(row)
        poll = self.video.poll_task(external)
        if poll.state == "SUCCEEDED":
            result = self.video.fetch_result(external)
            self._store(identifier, poll, result)
            return self._record(
                identifier,
                ProviderResult("ACKNOWLEDGED", external_id=external, billing=result.billing),
            )
        self._store(identifier, poll, None)
        if poll.state == "FAILED":
            return self._record(
                identifier,
                ProviderResult("FAILED", external_id=external, billing=poll.billing),
            )
        return self._record(identifier, ProviderResult("ACKNOWLEDGED", external_id=external))

    def cancel(self, identifier: UUID) -> dict[str, Any]:
        not_sent = False
        with business_transaction(self.content.engine, self.content.identity) as s:
            row = self.repo.job(s, identifier, lock=True)
            task = self._task(s, identifier, lock=True)
            if task is not None:
                if task["state"] in TERMINAL:
                    return self.repo.view(row)
                if not can_transition(task["state"], "CANCELLED"):
                    raise ApiError(ErrorCode.RESULT_UNKNOWN, "当前阶段不能停止视频任务")
                s.execute(
                    text("""UPDATE video_generation_tasks SET state='CANCELLED',
                    error_code='LOCAL_CANCELLED',version=version+1,
                    updated_at=clock_timestamp() WHERE tenant_id=:tenant AND job_id=:job"""),
                    dict(tenant=self.content.identity.tenant_id, job=identifier),
                )
                # 只停止本地轮询，不宣称供应商已停止或已退款；费用仍按已知回执/UNKNOWN保留。
                return self.repo.view(row)
            not_sent = row["submit_state"] == "NOT_SENT"
        if not_sent:
            return self.cancel_unsent(identifier)
        raise ApiError(ErrorCode.RESULT_UNKNOWN, "已发送但缺少视频任务账本，仅可按原键查询")

    def get_task(self, identifier: UUID) -> dict[str, Any]:
        with business_transaction(self.content.engine, self.content.identity) as s:
            task = self._task(s, identifier)
            if task is None:
                raise ApiError(ErrorCode.NO_DATA, "视频任务账本不存在")
            return dict(
                id=str(task["id"]),
                state=task["state"],
                externalTaskId=task["external_task_id"],
                pricingMode=task["pricing_mode"],
                requestedSeconds=task["requested_seconds"],
                billedSeconds=task["billed_seconds"],
                pollCount=task["poll_count"],
                resultMime=task["result_mime"],
                resultSizeBytes=task["result_size_bytes"],
                resultSha256=task["result_sha256"],
                resultDurationSeconds=task["result_duration_seconds"],
                errorCode=task["error_code"],
                version=task["version"],
            )
