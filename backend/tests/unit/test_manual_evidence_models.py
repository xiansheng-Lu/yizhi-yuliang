from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.execution.manual_evidence_models import ManualEvidenceInput


def value(**changes: object) -> ManualEvidenceInput:
    return ManualEvidenceInput.model_validate(
        dict(
            request_key=uuid4(),
            attempt_id=uuid4(),
            note_kind="FOLLOW_UP",
            reference_code="support-42",
            note_text="人工检查记录",
        )
        | changes
    )


def test_human_statement_only_and_normalized() -> None:
    assert value(note_text="  人工检查记录  ").note_text == "人工检查记录"
    assert value().receipt_id is None


@pytest.mark.parametrize(
    "text",
    [
        "Bearer private-sensitive-marker",
        "access_token=private-sensitive-marker",
        "密码：private-sensitive-marker",
        "Ｂｅａｒｅｒ private-sensitive-marker",
        "api_key: private-sensitive-marker",
        "Authorization: Basic private-sensitive-marker",
        "sk-private-sensitive-marker",
        "eyJhbGc.eyJzdWI.c2ln",
    ],
)
def test_recognized_credentials_rejected_without_echo(text: str) -> None:
    with pytest.raises(ValidationError) as error:
        value(note_text=text)
    assert "private-sensitive-marker" not in str(error.value)


@pytest.mark.parametrize(
    "field", ["status", "accepted", "amount_minor", "evidence_level", "source"]
)
def test_note_never_accepts_execution_or_money_claim_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        value(**{field: "arbitrary"})


def test_controls_paths_and_normalized_length_are_bounded() -> None:
    for changes in (
        dict(note_text="bad\x1bcontrol"),
        dict(reference_code="../../private.txt"),
        dict(note_text="ﷺ" * 2000),
        dict(note_kind="CLOSE_AND_RELEASE"),
    ):
        with pytest.raises(ValidationError):
            value(**changes)
