"""合成端口离线验证，不构成官方口径或真实业务验收。"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.http import HttpExchange
from yuliang.modules.connectors.plan_protocol import plan_page
from yuliang.modules.delivery.plan_proofs import PlanMeasureProof, measure_plan


def sample() -> dict[str, object]:
    return dict(
        campaign_id=11,
        adgroup_id=12,
        adgroup_name="合成广告组",
        configured_status="AD_STATUS_NORMAL",
        daily_budget=0,
        total_budget=100,
        bid_amount=0,
        created_time=1,
        last_modified_time=1,
        is_deleted=False,
        billing_event="BILLINGEVENT_CLICK",
        optimization_goal="OPTIMIZATIONGOAL_CLICK",
        deep_conversion_worth_rate="1.234567890123",
        private_field="discard",
    )


def parsed(row: dict[str, object]) -> dict[str, object]:
    response = HttpExchange(
        uuid4(),
        "adq.plans.adgroup",
        200,
        1,
        datetime.now(UTC),
        {},
        json.dumps(
            dict(
                code=0,
                data=dict(
                    list=[row], page_info=dict(page=1, page_size=1, total_number=1, total_page=1)
                ),
            )
        ).encode(),
    )
    ctx = cast(GatewayContext, SimpleNamespace(native_account_id="123"))
    return plan_page(ctx, "ADGROUP", response, 1, 1).sync.records[0].values


def proof() -> PlanMeasureProof:
    now = datetime.now(UTC)
    evidence = dict(ref="synthetic-unit-proof", sha256="a" * 64)
    return PlanMeasureProof(
        source_id=uuid4(),
        binding_version=1,
        version=1,
        level="ADGROUP",
        currency="CNY",
        budget_field="daily_budget",
        budget_unit="CNY_MINOR",
        bid_unit="CNY_MINOR",
        billing_events=("BILLINGEVENT_CLICK",),
        optimization_goals=("OPTIMIZATIONGOAL_CLICK",),
        roi_scale="RATIO",
        roi_attribution="synthetic-window",
        source_epoch="SECONDS",
        source_timezone="UTC",
        status_map={"AD_STATUS_NORMAL": "RUNNING"},
        evidence={
            k: evidence for k in ("budget", "bid", "currency", "roi", "source_time", "status")
        },
        checked_by="offline-test",
        checked_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=1),
        max_age_seconds=60,
    )


def test_observation_preserves_exact_decimal_and_drops_unknown() -> None:
    values = parsed(sample())
    assert values["deep_conversion_worth_rate"] == "1.234567890123"
    assert values["daily_budget"] == "0" and "private_field" not in values


@pytest.mark.parametrize(
    "field,value",
    [
        ("bid_amount", True),
        ("daily_budget", 1.2),
        ("is_deleted", 0),
        ("account_id", 124),
        ("adgroup_id", None),
        ("deep_conversion_worth_rate", "NaN"),
    ],
)
def test_invalid_protocol_fails_closed(field: str, value: object) -> None:
    row = sample() | {field: value}
    with pytest.raises(ValueError):
        parsed(row)


def test_valid_independent_proof_keeps_true_zero() -> None:
    p = proof()
    result, gaps = measure_plan(
        parsed(sample()),
        p,
        source_id=p.source_id,
        binding_version=1,
        fetched_at=datetime.now(UTC),
        now=datetime.now(UTC),
    )
    assert not gaps and result is not None
    assert result["budgetMinor"] == result["bidMinor"] == 0


@pytest.mark.parametrize("change", ["expired", "binding", "deleted", "missing", "unsafe"])
def test_unconfirmed_semantics_never_project(change: str) -> None:
    p, values = proof(), parsed(sample())
    now = datetime.now(UTC)
    version = 1
    if change == "expired":
        p = p.model_copy(update={"expires_at": now - timedelta(seconds=1)})
    elif change == "binding":
        version = 2
    elif change == "deleted":
        values["is_deleted"] = None
    elif change == "missing":
        values["daily_budget"] = None
    else:
        values["bid_amount"] = "9007199254740992"
    result, gaps = measure_plan(
        values, p, source_id=p.source_id, binding_version=version, fetched_at=now, now=now
    )
    assert result is None and gaps


def test_field_sources_cannot_be_omitted() -> None:
    with pytest.raises(ValidationError):
        PlanMeasureProof.model_validate(proof().model_dump() | {"evidence": {}})


def test_negative_native_sentinel_retained_but_not_money() -> None:
    values = parsed(sample() | {"daily_budget": -1})
    assert values["daily_budget"] == "-1"
    p = proof()
    result, gaps = measure_plan(
        values,
        p,
        source_id=p.source_id,
        binding_version=1,
        fetched_at=datetime.now(UTC),
        now=datetime.now(UTC),
    )
    assert result is None and "budgetMinor_SOURCE_MISSING" in gaps


def test_bid_and_roi_proof_cannot_cross_objective_or_billing_event() -> None:
    values = parsed(sample() | {"billing_event": "BILLINGEVENT_IMPRESSION"})
    p = proof()
    result, gaps = measure_plan(
        values,
        p,
        source_id=p.source_id,
        binding_version=1,
        fetched_at=datetime.now(UTC),
        now=datetime.now(UTC),
    )
    assert result is None and "BID_OR_ROI_APPLICABILITY_NOT_CONFIRMED" in gaps
