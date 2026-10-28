"""上传计划仅接受业务元数据，不接受客户端对象key或私有桶。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MIMES = {
    "image": frozenset({"image/png", "image/jpeg", "image/webp"}),
    "video": frozenset({"video/mp4", "video/webm"}),
    "audio": frozenset({"audio/mpeg", "audio/wav", "audio/ogg"}),
    "text": frozenset({"text/plain"}),
}
SIZES = {"image": 20971520, "video": 104857600, "audio": 52428800, "text": 1048576}


class UploadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    clientRequestKey: str
    name: str = Field(pattern=r"^[^\x00-\x1f]{1,200}$")
    kind: Literal["image", "video", "audio", "text"]
    productId: str = ""
    documentId: str | None = None
    mime: str
    size: int = Field(ge=1, le=104857600)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_upload(self) -> "UploadCreate":
        if self.kind in {"audio", "video"}:
            raise ValueError("音视频待媒体探测卡，当前仅开放图片与UTF8文本上传确认")
        UUID(self.clientRequestKey)
        if self.productId:
            UUID(self.productId)
        if self.documentId:
            UUID(self.documentId)
        if self.mime not in MIMES[self.kind] or self.size > SIZES[self.kind]:
            raise ValueError("文件类型或大小不在本地上传策略范围")
        return self
