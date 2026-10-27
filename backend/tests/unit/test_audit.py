from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.audit.service import AuditEntry


@pytest.mark.parametrize(
    "detail",
    [
        {"password": "never-reflect-private"},
        {"changed_fields": ["never-reflect-private"]},
        {"result": "never-reflect-private"},
    ],
)
def test_audit_rejects_sensitive_free_text(detail: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as error:
        AuditEntry.model_validate(
            {
                "action": "shop.update",
                "object_type": "shop",
                "object_id": uuid4(),
                "request_id": uuid4(),
                "detail": detail,
            }
        )
    assert "never-reflect-private" not in str(error.value)
