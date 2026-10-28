"""公开报告token在ASGI入口转为私有状态，路由及通用错误不携带原query。"""

from urllib.parse import parse_qs

from pydantic import SecretStr
from starlette.types import ASGIApp, Receive, Scope, Send


class PublicReportPrivacy:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/reports/public/"):
            raw = scope.get("query_string", b"")
            scope["query_string"] = b""
            try:
                if len(raw) > 128:
                    await self.app(scope, receive, send)
                    return
                values = parse_qs(raw.decode("ascii"), keep_blank_values=True, max_num_fields=1)
                tokens = values.get("token", [])
                if len(raw) <= 128 and set(values) == {"token"} and len(tokens) == 1:
                    scope.setdefault("state", {})["report_public_token"] = SecretStr(tokens[0])
            except (ValueError, UnicodeError):
                pass
        await self.app(scope, receive, send)
