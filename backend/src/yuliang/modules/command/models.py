from pydantic import Field, field_validator

from yuliang.modules.workspace.models import ReportingScope, StrictModel


class ParseInput(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    scope: ReportingScope

    @field_validator("text")
    @classmethod
    def trimmed_text(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value.encode("utf-16-le")) > 1000:
            raise ValueError("指令需要1至500字符")
        return value
