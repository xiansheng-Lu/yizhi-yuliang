"""本地批准派发验收辅助；只监听随机loopback端口，不包含生产装配。"""

import json
import os
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field


class CanonicalWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    object_id: str
    currency: Literal["CNY"]
    budget_minor: int = Field(strict=True)


def build_runtime() -> Any:
    """仅此测试Worker入口读取合成seed；生产runtime没有该入口或环境覆盖。"""
    from pydantic import SecretStr

    from yuliang.modules.connectors.archive import PlatformArchive, RecordedPlatformClient
    from yuliang.modules.connectors.base import GatewayContext, ObjectRef
    from yuliang.modules.connectors.http import OperationSpec, PlatformHttpClient
    from yuliang.modules.connectors.outcomes import BusinessVerdict
    from yuliang.modules.execution.dispatch_runtime import DispatchExecutor, DispatchRuntime
    from yuliang.worker import tasks

    seed_path = Path(os.environ["YULIANG_TEST_APPROVED_SEED"])
    seed = json.loads(seed_path.read_text())
    runtime = DispatchRuntime()
    if seed.get("closed"):
        return runtime

    def signer(wire: httpx.Request, credential: SecretStr) -> httpx.Request:
        wire.headers["Authorization"] = "Bearer " + credential.get_secret_value()
        return wire

    def decoder(response: Any) -> Any:
        value = json.loads(response.body)
        return BusinessVerdict(
            "CONFIRMED_SUCCESS" if value.get("code") == 0 else "CONFIRMED_REJECTION",
            "PARAMETER",
            value.get("trace_id"),
        )

    class SyntheticBudgetSerializer:
        def __init__(self, spec: Any) -> None:
            self.spec = spec

        def render(self, ctx: Any, job: Any, platform_key: UUID) -> Any:
            assert platform_key and job.action_type == "CHANGE_DAILY_BUDGET"
            return self.spec, dict(
                account_id=ctx.native_account_id,
                object_id="456",
                currency="CNY",
                budget_minor=job.new_value.amount_minor,
            )

    def crash_once(stage: str) -> None:
        if seed.get("crash") != stage:
            return
        marker = seed_path.with_suffix("." + stage)
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return
        os.close(descriptor)
        deadline = time.monotonic() + 20
        while not seed_path.with_suffix(".allow_exit").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("测试父进程未释放退出屏障")
            time.sleep(0.05)
        os._exit(91)  # 仅测试隔离Worker的实际进程故障。

    class CrashArchive(PlatformArchive):
        def append(self, *args: Any, **kwargs: Any) -> Any:
            if kwargs.get("phase") == "RESPONSE" or "RESPONSE" in args:
                crash_once("before_response_persist")
            return super().append(*args, **kwargs)

    class CrashClient(RecordedPlatformClient):
        async def send(self, *args: Any, **kwargs: Any) -> Any:
            crash_once("after_prepare")
            return await super().send(*args, **kwargs)

    def factory(session: Any, identity: Any, event: Any) -> Any:
        raw = dict(seed["ctx"])
        for key in ("shop_id", "account_id", "authorization_id", "request_id", "binding_id"):
            raw[key] = UUID(raw[key])
        raw["identity"] = identity
        ctx = GatewayContext(**raw)
        wire = dict(seed["spec"])
        wire["preflight_resources"] = tuple(wire.get("preflight_resources", ()))
        spec = OperationSpec(**wire, parameters_model=CanonicalWrite)
        settings = tasks.app.conf.yuliang_settings
        key = settings.auth_audit_secret or settings.session_secret
        client = CrashClient(
            PlatformHttpClient(signer, transport=LoopbackTransport(seed["endpoint"])),
            CrashArchive(tasks.engine(), key),
            decoder=decoder,
        )
        return DispatchExecutor(
            ctx=ctx,
            serializer=SyntheticBudgetSerializer(spec),
            client=client,
            credential=lambda: SecretStr("synthetic-pipeline-only"),
            target=ObjectRef.model_validate(seed["target"]),
        )

    runtime.register("CHANGE_DAILY_BUDGET", "tencent", "adq", factory)
    return runtime


if os.environ.get("YULIANG_TEST_APPROVED_SEED"):
    from celery.signals import task_postrun  # type: ignore[import-untyped]

    from yuliang.worker import tasks as _tasks
    from yuliang.worker.app import app as app

    def diagnosed_runtime() -> Any:
        try:
            return build_runtime()
        except Exception as error:
            import traceback

            Path(os.environ["YULIANG_TEST_APPROVED_SEED"]).with_suffix(".diagnostic").write_text(
                json.dumps(
                    {
                        "bootstrap": type(error).__name__,
                        "frames": [
                            (Path(frame.filename).name, frame.lineno, frame.name)
                            for frame in traceback.extract_tb(error.__traceback__)[-6:]
                        ],
                    }
                )
            )
            raise

    _tasks.approved_execution_runtime = diagnosed_runtime

    from yuliang.modules.execution import recovery_schedule as _schedule

    _original_sender = _schedule.celery_recovery_sender

    def recovery_confirmation_probe(client: Any) -> Any:
        sender = _original_sender(client)

        def send(*args: Any, **kwargs: Any) -> None:
            sender(*args, **kwargs)
            seed_path = Path(os.environ["YULIANG_TEST_APPROVED_SEED"])
            seed = json.loads(seed_path.read_text())
            if seed.get("recovery_confirmation_lost"):
                try:
                    descriptor = os.open(
                        seed_path.with_suffix(".recovery_confirmation_lost"),
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o600,
                    )
                except FileExistsError:
                    return
                os.close(descriptor)
                raise ConnectionError("synthetic recovery publish confirmation lost")

        return send

    _schedule.celery_recovery_sender = recovery_confirmation_probe

    from yuliang.modules.execution import approval_outbox as _origin
    from yuliang.modules.execution.dispatch_service import ApprovedExecutionDispatch as _Dispatch

    _original_handle = _Dispatch.handle

    def diagnosed_handle(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return _original_handle(self, *args, **kwargs)
        except Exception as error:
            import traceback

            Path(os.environ["YULIANG_TEST_APPROVED_SEED"]).with_suffix(".diagnostic").write_text(
                json.dumps(
                    {
                        "type": type(error).__name__,
                        "sqlstate": getattr(getattr(error, "orig", None), "sqlstate", None),
                        "frames": [
                            (Path(frame.filename).name, frame.lineno, frame.name)
                            for frame in traceback.extract_tb(error.__traceback__)[-6:]
                        ],
                    }
                )
            )
            raise

    _Dispatch.handle = diagnosed_handle

    _original_load = _origin._load

    def diagnosed_load(*args: Any, **kwargs: Any) -> Any:
        try:
            return _original_load(*args, **kwargs)
        except Exception as error:
            code = getattr(error, "code", None)
            Path(os.environ["YULIANG_TEST_APPROVED_SEED"]).with_suffix(".diagnostic").write_text(
                json.dumps({"type": type(error).__name__, "code": str(code)})
            )
            raise

    _origin._load = diagnosed_load

    @task_postrun.connect(weak=False)
    def rejected_probe(state: str, **kwargs: Any) -> None:
        if state == "REJECTED":
            Path(os.environ["YULIANG_TEST_APPROVED_SEED"]).with_suffix(".rejected").write_text(
                json.dumps(
                    {
                        "state": "REJECTED",
                        "requeue": bool(getattr(kwargs.get("retval"), "requeue", False)),
                    }
                )
            )


class LoopbackTransport(httpx.AsyncBaseTransport):
    """在生产出口校验之后转接真实本机TCP；不放宽生产URL/签名器校验。"""

    def __init__(self, endpoint: str) -> None:
        url = httpx.URL(endpoint)
        assert url.scheme == "http" and url.host == "127.0.0.1" and url.port
        self.endpoint = endpoint
        self.transport = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        local = httpx.Request(
            "POST",
            self.endpoint + "/synthetic/execution/budget",
            content=request.content,
            headers={"Content-Type": "application/json"},
            extensions=request.extensions,
        )
        return await self.transport.handle_async_request(local)

    async def aclose(self) -> None:
        await self.transport.aclose()


class LoopbackWriteProbe(AbstractContextManager["LoopbackWriteProbe"]):
    """真实TCP HTTP记录；回调在另一连接核验SENT已提交，再返回合成受理。"""

    def __init__(self, before_response: Callable[[], None]) -> None:
        self.before_response = before_response
        self.calls: list[dict[str, Any]] = []
        self.errors: list[BaseException] = []
        self.lock = threading.Lock()
        probe = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 -- stdlib协议方法。
                try:
                    assert self.path == "/synthetic/execution/budget"
                    size = int(self.headers.get("Content-Length", "0"))
                    assert 0 < size <= 4096
                    body = json.loads(self.rfile.read(size))
                    probe.before_response()
                    with probe.lock:
                        probe.calls.append(body)
                    payload = b'{"code":0,"trace_id":"synthetic-pipeline-receipt"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except BaseException as error:
                    with probe.lock:
                        probe.errors.append(error)
                    self.send_error(500)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> "LoopbackWriteProbe":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "本fixture HTTP线程未退出"
