"""复用已实测的ADQ换码/刷新及小店自研stable_token协议；唯一刷新者为驭量。"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

from yuliang.modules.connectors.base import TokenSet
from yuliang.modules.connectors.oauth import (
    CodeExchangeFailure,
    OAuthApplication,
    OAuthExchangeContext,
)
from yuliang.modules.connectors.resources import ApplicationResource
from yuliang.platform.security.egress import private_http_logs, validate_destination


class NativeAuthorization:
    def __init__(
        self, resource: ApplicationResource, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.resource, self.transport = resource, transport

    def application(self) -> OAuthApplication:
        item = self.resource
        if item.external_token_service is not None:
            raise ValueError("托管令牌不创建驭量OAuth应用或虚构回调")
        if not item.enabled:
            raise ValueError("应用交接尚未完成，拒绝启用")
        return OAuthApplication(
            item.id,
            item.app_id,
            item.connector_id,
            item.provider,
            item.connector_id,
            item.redirect_uri or "",
            "OAUTH" if item.mode == "ADQ_OAUTH" else "SELF_CREDENTIAL",
        )

    def authorize_url(self, state: SecretStr) -> str:
        if self.resource.external_token_service is not None:
            raise ValueError("托管令牌由原服务授权，驭量不创建第二授权或刷新者")
        application = self.application()
        if self.resource.mode != "ADQ_OAUTH":
            raise ValueError("小店自研不使用ADQ扫码授权")
        return "https://developers.e.qq.com/oauth/authorize?" + urlencode(
            {
                "client_id": application.app_id,
                "redirect_uri": application.redirect_uri,
                "state": state.get_secret_value(),
            }
        )

    async def _request(self, *, code: SecretStr | None, refresh: SecretStr | None) -> TokenSet:
        item = self.resource
        if item.external_token_service is not None:
            raise CodeExchangeFailure(unknown=False)
        self.application()  # 先核验资源/唯一刷新者，再读取Secret或出网。
        secret = item.secret.resolve().get_secret_value()
        started = datetime.now(UTC)
        if item.mode == "ADQ_OAUTH":
            method, url = "GET", "https://api.e.qq.com/oauth/token"
            parameters = {"client_id": item.app_id, "client_secret": secret}
            if refresh is not None:
                parameters.update(
                    grant_type="refresh_token", refresh_token=refresh.get_secret_value()
                )
            elif code is not None:
                parameters.update(
                    grant_type="authorization_code",
                    authorization_code=code.get_secret_value(),
                    redirect_uri=self.application().redirect_uri,
                )
            else:
                raise CodeExchangeFailure(unknown=False)
            kwargs: dict[str, Any] = {"params": parameters}
        else:
            method, url = "POST", "https://api.weixin.qq.com/cgi-bin/stable_token"
            kwargs = {
                "json": {
                    "grant_type": "client_credential",
                    "appid": item.app_id,
                    "secret": secret,
                    "force_refresh": False,
                }
            }
        parsed = httpx.URL(url)
        validate_destination(parsed, item.provider, parsed.path)
        try:
            with private_http_logs():
                async with (
                    asyncio.timeout(30),
                    httpx.AsyncClient(
                        transport=self.transport,
                        trust_env=False,
                        follow_redirects=False,
                        timeout=20,
                    ) as client,
                ):
                    async with client.stream(method, url, **kwargs) as response:
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 256 * 1024:
                                raise CodeExchangeFailure()
                        if response.status_code != 200:
                            raise CodeExchangeFailure(unknown=response.status_code >= 500)
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
            status = data.get("code" if item.mode == "ADQ_OAUTH" else "errcode")
            # stable_token成功通常没有errcode；access_token和expires_in仍必须完整。
            if item.mode == "WXSHOP_SELF" and status is None and "access_token" in data:
                status = 0
            if type(status) is not int or status != 0:
                raise CodeExchangeFailure(unknown=False)
            payload = data.get("data") if item.mode == "ADQ_OAUTH" else data
            if not isinstance(payload, dict):
                raise ValueError()
            ttl = payload.get(
                "access_token_expires_in" if item.mode == "ADQ_OAUTH" else "expires_in"
            )
            token = payload.get("access_token")
            if type(ttl) is not int or not 1 <= ttl <= 366 * 86400:
                raise ValueError()
            if not isinstance(token, str) or not 1 <= len(token) <= 16384:
                raise ValueError()
            expires = started + timedelta(seconds=ttl)
            if expires <= datetime.now(UTC):
                raise ValueError()
            info = payload.get("authorizer_info", {})
            if (
                item.mode == "ADQ_OAUTH"
                and code is not None
                and (not isinstance(info, dict) or str(info.get("account_id")) != item.native_id)
            ):
                raise CodeExchangeFailure(unknown=False)
            scopes = info.get("scope_list", []) if isinstance(info, dict) else []
            if not isinstance(scopes, list) or any(not isinstance(value, str) for value in scopes):
                raise ValueError()
            if item.mode == "WXSHOP_SELF":
                # 自研token不返回scope；只使用交接件中有后台证据的实际权限，不从接口目录推定。
                scopes = list(item.granted_scopes or ())
            new_refresh = payload.get("refresh_token")
            return TokenSet(
                access_token=SecretStr(token),
                refresh_token=SecretStr(new_refresh) if new_refresh else refresh,
                expires_at=expires,
                scopes=tuple(scopes),
            )
        except CodeExchangeFailure:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
            # 不把携带query凭据的HTTP错误/响应原文传入日志或公共异常。
            raise CodeExchangeFailure() from None

    async def exchange(self, context: OAuthExchangeContext, code: SecretStr) -> TokenSet:
        if (
            context.application != self.application()
            or context.identity.tenant_id != self.resource.tenant_id
        ):
            raise CodeExchangeFailure(unknown=False)
        return await self._request(code=code, refresh=None)

    async def renew(self, refresh: SecretStr | None) -> TokenSet:
        if self.resource.mode == "ADQ_OAUTH" and refresh is None:
            raise CodeExchangeFailure(unknown=False)
        return await self._request(code=None, refresh=refresh)
