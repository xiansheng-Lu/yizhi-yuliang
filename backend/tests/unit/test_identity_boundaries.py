from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.access.management import ScopeGrant
from yuliang.platform.db import VerifiedIdentity


@pytest.mark.parametrize("tenant", [0, -1, True, "1", "1; SELECT 1", 2**63])
def test_verified_identity_rejects_non_server_bigint_context(tenant: Any) -> None:
    with pytest.raises(ValueError):
        VerifiedIdentity(tenant, uuid4(), uuid4())


@pytest.mark.parametrize(
    "scope",
    [
        {"scopeType": "SHOP"},
        {"scopeType": "SHOP", "shopId": str(uuid4()), "accountId": str(uuid4())},
        {"scopeType": "ALL_TENANT", "shopId": str(uuid4())},
        {"scopeType": "ALL_TENANT", "role": "OWNER"},
    ],
)
def test_scope_input_cannot_smuggle_additional_target_or_role(scope: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ScopeGrant.model_validate(scope)
