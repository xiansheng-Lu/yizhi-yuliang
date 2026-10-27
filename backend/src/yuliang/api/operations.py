"""运维探针仅信实际来源网段，忽略客户端转发头；指标无租户标签或凭据。"""

from ipaddress import ip_address, ip_network

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, PlainTextResponse

from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode
from yuliang.platform.probes import ProbeService

router = APIRouter()


def probes(request: Request) -> ProbeService:
    try:
        source = ip_address(request.client.host if request.client else "")
    except ValueError:
        raise ApiError(ErrorCode.FORBIDDEN, "运维来源未获允许") from None
    if not any(
        source in ip_network(network)
        for network in request.app.state.settings.operations_allowed_networks
    ):
        raise ApiError(ErrorCode.FORBIDDEN, "运维来源未获允许")
    instance: ProbeService = request.app.state.probes
    return instance


@router.get("/readyz", include_in_schema=False)
def readyz(request: Request) -> JSONResponse:
    snapshot = probes(request).snapshot()
    return success(request, snapshot.data(), status_code=200 if snapshot.ready else 503)


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> PlainTextResponse:
    snapshot = probes(request).snapshot()
    return PlainTextResponse(snapshot.metrics(), media_type="text/plain; version=0.0.4")
