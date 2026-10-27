from typing import Literal

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse


class Success[T](BaseModel):
    schemaVersion: Literal["command-job-v1.0"] = "command-job-v1.0"
    requestId: str
    data: T


def success[T](request: Request, data: T, *, status_code: int = 200) -> JSONResponse:
    payload = Success[T](requestId=request.state.request_id, data=data)
    return JSONResponse(
        jsonable_encoder(payload),
        status_code=status_code,
        headers={"X-Request-Id": request.state.request_id, "Cache-Control": "no-store"},
    )
