"""ASGI入口即移除回调query，含失败路由；仅以SecretStr传给实际回调。"""

from urllib.parse import parse_qs

from pydantic import SecretStr
from starlette.types import ASGIApp, Receive, Scope, Send


class OAuthCallbackPrivacy:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/v1/connections/oauth/"):
            raw = scope.get("query_string", b"")
            scope["query_string"] = b""
            values = {}
            try:
                if len(raw) <= 16384:
                    parsed = parse_qs(raw.decode("ascii"), keep_blank_values=True, max_num_fields=8)
                    for name in ("state", "authorization_code"):
                        items = parsed.get(name, [])
                        if len(items) == 1 and len(items[0]) <= 8192:
                            values[name] = SecretStr(items[0])
            except (ValueError, UnicodeError):
                values = {}
            scope.setdefault("state", {})["platform_oauth_query"] = values
        await self.app(scope, receive, send)
