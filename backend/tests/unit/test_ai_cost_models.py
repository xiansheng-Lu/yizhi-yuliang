"""报价协议严格区分未知与明确零元，拒绝无回执账单。"""

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.aikernel.cost_models import BillableRequest, BillingOutcome


@pytest.mark.parametrize(
    "values",
    [
        {"state": "UNKNOWN", "amount_minor": 0},
        {"state": "KNOWN", "amount_minor": 0, "currency": "CNY"},
        {"state": "KNOWN", "amount_minor": -1, "currency": "CNY", "proof_hash": "a" * 64},
        {"state": "KNOWN", "amount_minor": True, "currency": "CNY", "proof_hash": "a" * 64},
        {"state": "KNOWN", "amount_minor": 1, "currency": "EUR", "proof_hash": "a" * 64},
    ],
)
def test_m5_02a_unknown_or_invalid_receipt_is_not_zero(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BillingOutcome.model_validate(values)
    assert BillingOutcome().amount_minor is None


def test_m5_02a_request_fingerprint_binds_amount_and_payload() -> None:
    req = BillableRequest(
        request_key=uuid4(),
        kind="LLM",
        scenario="copywriting",
        price_key="example.primary",
        input_units=3,
        output_units=2,
        payload_hash="a" * 64,
    )
    assert (
        req.fingerprint()
        == BillableRequest.model_validate_json(req.model_dump_json()).fingerprint()
    )
    assert req.fingerprint() != req.model_copy(update={"output_units": 3}).fingerprint()
    assert req.fingerprint() != req.model_copy(update={"payload_hash": "b" * 64}).fingerprint()
    with pytest.raises(ValidationError):
        BillableRequest.model_validate(req.model_dump() | {"input_units": True})
