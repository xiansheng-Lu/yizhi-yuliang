import json
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from yuliang.modules.aikernel.models import Candidate, Completion, ModelSpec, ServerSecrets, Usage


def test_model_configuration_contains_only_server_references() -> None:
    spec = ModelSpec(
        model_key="example.primary",
        provider="example",
        model="example-v1",
        endpoint_ref="PRIMARY_ENDPOINT",
        secret_ref="PRIMARY_KEY",
        billing_currency="CNY",
    )
    value = "NeverExposePrivateCredential"
    store = ServerSecrets({"PRIMARY_KEY": SecretStr(value)})
    resolved = store.resolve(spec)
    assert resolved.get_secret_value() == value
    assert value not in repr(store) + repr(spec) + repr(resolved) + json.dumps(spec.model_dump())
    with pytest.raises(ValidationError) as error:
        ModelSpec.model_validate(spec.model_dump() | {"api_key": value})
    assert value not in str(error.value)
    with pytest.raises(ValueError, match="未配置"):
        ServerSecrets({}).resolve(spec)


@pytest.mark.parametrize(
    "bad",
    [
        {"tokens_in": 0},
        {"cost_minor": 0},
        {"usage_state": "KNOWN", "tokens_in": 0},
        {"cost_state": "KNOWN"},
        {"cost_state": "KNOWN", "cost_minor": 3},
        {"cost_currency": "USD"},
        {"cost_state": "KNOWN", "cost_minor": True},
        {"usage_state": "KNOWN", "tokens_in": -1, "tokens_out": 0},
        {"cost_state": "KNOWN", "cost_minor": 9223372036854775808},
    ],
)
def test_unknown_usage_or_cost_cannot_be_filled_with_zero(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Usage.model_validate(bad)


def test_only_explicit_known_zero_is_zero() -> None:
    value = Usage(
        usage_state="KNOWN",
        tokens_in=0,
        tokens_out=0,
        cost_state="KNOWN",
        cost_minor=0,
        cost_currency="CNY",
    )
    assert value.cost_minor == 0
    assert Usage().tokens_in is None and Usage().tokens_out is None and Usage().cost_minor is None


@pytest.mark.parametrize("status", ["FAILED", "TIMEOUT", "RATE_LIMITED", "FALLBACK"])
def test_failure_completion_defaults_to_unknown(status: str) -> None:
    result = Completion.model_validate({"status": status, "latency_ms": 7})
    assert result.usage.model_dump() == {
        "usage_state": "UNKNOWN",
        "cost_state": "UNKNOWN",
        "tokens_in": None,
        "tokens_out": None,
        "cost_minor": None,
        "cost_currency": None,
    }


def test_model_candidate_cannot_skip_review_or_invent_response() -> None:
    with pytest.raises(ValidationError):
        Candidate.model_validate(
            {"call_id": uuid4(), "source": "MODEL", "status": "CANDIDATE", "requires_review": False}
        )
    with pytest.raises(ValidationError):
        Completion(status="OK", latency_ms=1)
