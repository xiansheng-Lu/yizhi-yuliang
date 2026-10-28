from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest

from yuliang.modules.reporting.query_models import QueryRequest
from yuliang.modules.reporting.query_plans import project_adgroups as project
from yuliang.platform.errors import ApiError, ErrorCode


def project_adgroups(view: dict[str, Any], value: QueryRequest, synced_at: str) -> Any:
    return project(
        view, value, synced_at, resolved_account_id="resolved-account", resolved_tenant_id=1
    )


def request(**filters: str) -> QueryRequest:
    return QueryRequest.model_validate(
        dict(
            scope=dict(
                tenantId="1",
                businessEntityId=str(uuid4()),
                shopId=str(uuid4()),
                provider="tencent",
                productLine="adq",
                accountId=str(uuid4()),
                start="2026-10-01",
                end="2026-10-02",
            ),
            resource="adgroups",
            filters=filters,
        )
    )


def observation(native: str = "9007199254740993", parent: str = "10") -> dict[str, Any]:
    return dict(
        nativeId=native,
        values=dict(
            level="ADGROUP",
            adgroup_id=native,
            campaign_id=parent,
            adgroup_name="真实来源名称",
            configured_status="AD_STATUS_NORMAL",
            is_deleted=False,
        ),
    )


def view() -> dict[str, Any]:
    return dict(
        status="coverage_error",
        observations=[observation()],
        normalized=[],
        gaps=["PLAN_MEASURE_PROOF_MISSING"],
    )


def test_unknown_units_and_times_stay_unknown_not_zero() -> None:
    page = project_adgroups(view(), request(), "2026-10-28T00:00:00Z")
    row = page.rows[0]
    assert row.native_id == "9007199254740993" and row.metrics == {"budgetMinor": None}
    assert row.updated_at == "源更新时间未提供" and row.version is None and row.money_type is None
    assert row.status == "AD_STATUS_NORMAL" and page.next_cursor is None
    assert any("CURRENT_CONFIG" in warning for warning in page.warnings)


def test_valid_measured_zero_preserved_without_target_roi_as_effect() -> None:
    data = view()
    data.update(
        status="ready",
        gaps=[],
        normalized=[
            dict(
                nativeId="9007199254740993",
                values={
                    "budgetMinor": 0,
                    "status": "RUNNING",
                    "updatedAt": "2026-10-27T00:00:00Z",
                    "targetRoi": 1.25,
                },
            )
        ],
    )
    row = project_adgroups(data, request(), "2026-10-28T00:00:00Z").rows[0]
    assert row.metrics == {"budgetMinor": 0} and row.money_type == "AD_BUDGET"
    assert row.status == "RUNNING" and row.updated_at == "2026-10-27T00:00:00Z"


def test_parent_native_id_and_search_do_not_generate_rows() -> None:
    data = view()
    assert project_adgroups(data, request(nativeParentId="20"), "2026-10-28T00:00:00Z").rows == []
    assert (
        len(
            project_adgroups(
                data, request(nativeParentId="10", search="来源"), "2026-10-28T00:00:00Z"
            ).rows
        )
        == 1
    )
    assert project_adgroups(data, request(search="假名称"), "2026-10-28T00:00:00Z").rows == []


@pytest.mark.parametrize("status", ["not_connected", "error"])
def test_incomplete_or_missing_source_not_empty(status: str) -> None:
    data = view() | dict(status=status, observations=[])
    with pytest.raises(ApiError) as error:
        project_adgroups(data, request(), "2026-10-28T00:00:00Z")
    assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE


def test_real_empty_requires_success_and_has_actual_sync_time() -> None:
    page = project_adgroups(
        dict(status="empty", observations=[], normalized=[], gaps=[]),
        request(),
        "2026-10-28T00:00:00Z",
    )
    assert page.rows == [] and page.synced_at == "2026-10-28T00:00:00Z"


@pytest.mark.parametrize("invalid", ["duplicate", "level", "name"])
def test_identity_or_required_name_is_not_invented(invalid: str) -> None:
    data = deepcopy(view())
    if invalid == "duplicate":
        data["observations"] *= 2
    else:
        data["observations"][0]["values"]["level" if invalid == "level" else "adgroup_name"] = ""
    with pytest.raises(ApiError) as error:
        project_adgroups(data, request(), "2026-10-28T00:00:00Z")
    assert error.value.code == ErrorCode.DATA_COVERAGE_INSUFFICIENT


def test_limit_never_silently_truncates_and_can_narrow() -> None:
    data = view() | dict(observations=[observation(str(i), str(i % 2 + 1)) for i in range(1, 1002)])
    assert len(project_adgroups(data, request(), "2026-10-28T00:00:00Z").rows) == 1001
    assert (
        len(project_adgroups(data, request(nativeParentId="1"), "2026-10-28T00:00:00Z").rows) == 500
    )
    data["observations"] = [observation(str(i)) for i in range(1, 10002)]
    with pytest.raises(ApiError):
        project_adgroups(data, request(), "2026-10-28T00:00:00Z")


@pytest.mark.parametrize("parent", ["0", "01", "9223372036854775808", "local-uuid"])
def test_parent_requires_exact_native_id(parent: str) -> None:
    with pytest.raises(ApiError) as error:
        project_adgroups(view(), request(nativeParentId=parent), "2026-10-28T00:00:00Z")
    assert error.value.code == ErrorCode.INVALID_INPUT


def test_stable_id_uses_resolved_tenant_account_not_optional_request_account() -> None:
    value = request()
    blank = value.model_copy(update=dict(scope=value.scope.model_copy(update=dict(account_id=""))))

    def row(request_value: QueryRequest, account: str, tenant: int) -> Any:
        return project(
            view(),
            request_value,
            "2026-10-28T00:00:00Z",
            resolved_account_id=account,
            resolved_tenant_id=tenant,
        ).rows[0]

    assert row(value, "a", 1).id == row(blank, "a", 1).id
    assert row(blank, "a", 1).id != row(blank, "b", 1).id
    assert row(blank, "a", 1).id != row(blank, "a", 2).id
