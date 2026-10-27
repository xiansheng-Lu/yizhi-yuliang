"""注册表只接受代码显式装配的适配器，不按provider推断支持产品线。"""

import inspect

from yuliang.modules.connectors.base import GatewayContext, PlatformGateway
from yuliang.platform.errors import ApiError, ErrorCode

ENABLED_KEYS = frozenset({("tencent", "adq"), ("tencent", "wxpromo"), ("wxshop", "wxshop")})


class GatewayRegistry:
    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], PlatformGateway] = {}

    def register(self, provider: str, product_line: str, gateway: PlatformGateway) -> None:
        key = (provider, product_line)
        if (
            key not in ENABLED_KEYS
            or not isinstance(gateway, PlatformGateway)
            or not all(
                inspect.iscoroutinefunction(getattr(gateway, name, None))
                for name in (
                    "read",
                    "preview",
                    "submit",
                    "readback",
                    "refresh_token",
                    "capability_probe",
                )
            )
        ):
            raise ValueError("本轮未开放该适配器或端口不完整")
        if key in self._adapters:
            raise ValueError("适配器已注册，拒绝隐式覆盖")
        self._adapters[key] = gateway

    def resolve(self, ctx: GatewayContext) -> PlatformGateway:
        adapter = self._adapters.get((ctx.provider, ctx.adapter_product))
        if adapter is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "当前产品线尚未接入适配器")
        return adapter
