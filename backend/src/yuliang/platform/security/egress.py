"""固定平台出口、路径与上下文日志保护。"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

PLATFORM_HOSTS = {
    "tencent": frozenset({"api.e.qq.com"}),
    "wxshop": frozenset({"api.weixin.qq.com"}),
}
_platform_call: ContextVar[bool] = ContextVar("platform_http_call", default=False)


def validate_destination(url: httpx.URL, provider: str, path: str) -> None:
    if (
        url.scheme != "https"
        or url.host not in PLATFORM_HOSTS.get(provider, frozenset())
        or url.port not in (None, 443)
        or url.username
        or url.password
        or url.fragment
        or url.raw_path.split(b"?", 1)[0] != path.encode("ascii")
    ):
        raise ValueError("平台出口、路径或协议不在服务端白名单")


class _PrivateHttpLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _platform_call.get() and record.name.startswith(("httpx", "httpcore")):
            # 第三方库可能把query内的令牌或原始request对象插入日志；只保留级别/来源。
            record.msg = "平台HTTP库事件（请求细节已省略）"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


@contextmanager
def private_http_logs() -> Iterator[None]:
    token = _platform_call.set(True)
    guard = _PrivateHttpLogs()
    handlers = set(logging.getLogger().handlers)
    for name, value in logging.root.manager.loggerDict.copy().items():
        if name.startswith(("httpx", "httpcore")) and isinstance(value, logging.Logger):
            handlers.update(value.handlers)
    if logging.lastResort:
        handlers.add(logging.lastResort)
    for handler in handlers:
        handler.addFilter(guard)
    try:
        yield
    finally:
        for handler in handlers:
            handler.removeFilter(guard)
        _platform_call.reset(token)
