"""规则叙述必须可由真实冻结事实复核，不调用AI。"""

from typing import Any
from uuid import uuid4

import pytest
from test_analysis_calculator import facts, run

from yuliang.modules.analysis.narrative import rule_insights


def inputs() -> tuple[Any, list[dict[str, Any]], set[Any]]:
    rows = facts()
    account = uuid4()
    for i, row in enumerate(rows):
        row.update(
            id=i + 1,
            revision_number=1,
            account_id=str(account),
            sync_run_id=str(uuid4()),
            page_no=0,
            outbound_id=str(uuid4()),
        )
    return run(rows), rows, {account}


def test_rules_cite_actual_facts_without_model_or_profit() -> None:
    result, rows, accounts = inputs()
    insights = rule_insights(result, rows, accounts)
    assert insights and all(i["metricId"] in {"ads.spend", "ads.gmv", "ads.roi"} for i in insights)
    assert all("fact:" in i["evidence"] and "run:" in i["evidence"] for i in insights)
    assert not any("利润=" in i["evidence"] for i in insights)
    assert result.insight_source == "rules"


def test_foreign_account_and_missing_evidence_never_narrate() -> None:
    result, rows, _ = inputs()
    assert not rule_insights(result, rows, {uuid4()})
    for row in rows:
        row.pop("outbound_id")
    assert not rule_insights(result, rows, set())


def test_changed_metric_cannot_use_old_evidence() -> None:
    result, rows, accounts = inputs()
    result.metrics[0]["value"] = 999
    with pytest.raises(ValueError, match="NARRATIVE_METRIC_EVIDENCE_MISMATCH"):
        rule_insights(result, rows, accounts)
