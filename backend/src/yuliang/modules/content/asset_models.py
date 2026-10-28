"""素材快照证明和文档结构引用，未校验对象不能仅靠assetId通过。"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from yuliang.modules.content.models import Document


@dataclass(frozen=True)
class AssetProof:
    identifier: UUID
    tenant: int
    shop: UUID
    product: UUID | None
    kind: str
    key: str
    bucket: str
    version: str
    size: int
    mime: str
    sha256: str
    checked_at: datetime
    storage_token: str = field(default="", repr=False)


def document_assets(document: Document) -> dict[UUID, set[str]]:
    result: dict[UUID, set[str]] = {}

    def add(value: str, kind: str) -> None:
        if value:
            result.setdefault(UUID(value), set()).add(kind)

    for node in document.nodes:
        add(node.assetId, "ANY")
    for board in document.boards:
        for layer in board.layers:
            add(layer.assetId, "image" if layer.type == "image" else "ANY")
    for clip in document.clips:
        add(clip.assetId, clip.track if clip.track != "caption" else "ANY")
    for message in document.messages or []:
        for value in message.assetIds or []:
            add(value, "ANY")
    return result
