"""显式使用唯一托管刷新者；不持有refresh_token，不提供刷新操作。"""

from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, model_validator


class ExternalTokenUnavailable(RuntimeError):
    """不携带上游响应、凭据或网络异常细节。"""


class ExternalTokenConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    base_url: str
    api_key: SecretStr
    product: Literal["adq", "wxshop"]
    account_id: str | None = None

    @model_validator(mode="after")
    def check_boundary(self) -> "ExternalTokenConfig":
        url = urlsplit(self.base_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "::1"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
            or url.port is None
            or not self.api_key.get_secret_value().strip()
            or (self.product == "adq" and not self.account_id)
            or (self.product == "wxshop" and self.account_id is not None)
        ):
            raise ValueError("需要显式回环端口、密钥及对应产品账户配置")
        return self


class ExternalToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    remaining_seconds: int


class ExternalTokenProvider:
    def __init__(
        self, config: ExternalTokenConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._config = config
        self._transport = transport

    async def get_token(self) -> ExternalToken:
        params = {"raw": "1"}
        if self._config.account_id:
            params["account_id"] = self._config.account_id
        try:
            async with httpx.AsyncClient(
                transport=self._transport, trust_env=False, follow_redirects=False, timeout=5
            ) as client:
                response = await client.get(
                    self._config.base_url.rstrip("/") + "/api/token",
                    params=params,
                    headers={"X-API-Key": self._config.api_key.get_secret_value()},
                )
                if response.status_code != 200 or len(response.content) > 16384:
                    raise ValueError("invalid response")
                data = response.json()
                if (
                    not isinstance(data, dict)
                    or data.get("ok") is not True
                    or data.get("masked") is not False
                    or not isinstance(data.get("access_token"), str)
                    or not data["access_token"].strip()
                    or type(data.get("remaining")) is not int
                    or data["remaining"] <= 0
                    or (
                        self._config.product == "adq"
                        and data.get("account_id") != self._config.account_id
                    )
                ):
                    raise ValueError("invalid response")
                return ExternalToken(
                    access_token=SecretStr(data["access_token"]),
                    remaining_seconds=data["remaining"],
                )
        except (httpx.HTTPError, ValueError, TypeError):
            raise ExternalTokenUnavailable("托管令牌不可用；保持关闭") from None
