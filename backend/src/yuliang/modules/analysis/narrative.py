"""确定性证据叙述；仅引用当前任务冻结事实，不调用模型。"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from yuliang.modules.analysis.calculator import DEFINITIONS
from yuliang.modules.analysis.result_models import AnalysisResult


def rule_insights(
    result: AnalysisResult, snapshot: list[dict[str, Any]], accounts: set[UUID]
) -> list[dict[str, Any]]:
    allowed = {str(a) for a in accounts}
    output = []
    definitions = {
        d + "." + item[0]: (item[3], item[4]) for d, items in DEFINITIONS.items() for item in items
    }
    for metric in result.metrics:
        if metric["status"] != "ready" or metric["value"] is None:
            continue
        if metric["id"] not in {
            "ads.spend",
            "ads.gmv",
            "ads.roi",
            "shop.gmv",
            "shop.orders",
            "shop.refund",
        }:
            continue
        numerator, denominator = definitions[metric["id"]]
        names = {numerator, denominator} - {None}
        line = "adq" if metric["dimension"] == "ads" else "wxshop"
        sources = [
            r
            for r in snapshot
            if r.get("product_line") == line
            and r.get("metric_name") in names
            and str(r.get("account_id")) in allowed
            and result.query.start_date <= str(r.get("fact_date", "")) <= result.query.end_date
            and all(
                r.get(k) is not None
                for k in ("id", "revision_number", "sync_run_id", "page_no", "outbound_id")
            )
        ]
        if {r["metric_name"] for r in sources} != names:
            continue
        totals = {
            name: sum(
                (Decimal(str(r["value_num"])) for r in sources if r["metric_name"] == name),
                Decimal(0),
            )
            for name in names
        }
        value = totals[numerator]
        if denominator:
            if totals[denominator] <= 0:
                continue
            value /= totals[denominator]
        value = value.quantize(
            Decimal("1") if metric["unit"] in {"minor", "count"} else Decimal(".000001"),
            rounding=ROUND_HALF_UP,
        )
        if value != Decimal(str(metric["value"])):
            raise ValueError("NARRATIVE_METRIC_EVIDENCE_MISMATCH")
        refs = sorted(
            {
                f"fact:{r['id']}@r{r['revision_number']}/run:{r['sync_run_id']}/page:{r['page_no']}"
                for r in sources
            }
        )
        unit = {"minor": "分", "count": "次", "ratio": "", "rate": ""}.get(metric["unit"], "")
        output.append(
            dict(
                id="rules-" + metric["id"],
                kind="conclusion",
                title="已核对" + metric["name"],
                evidence=f"本期{metric['name']}={metric['value']}{unit}；依据冻结事实"
                + "；".join(refs[:20])
                + (f"；共{len(refs)}条，列出前20条" if len(refs) > 20 else ""),
                direction="仅说明本期同口径观测，不推断利润或增长因果。",
                action="核对来源口径及本期表现后制定下一步验证。",
                priority="P3",
                verificationMetric=metric["id"],
                metricId=metric["id"],
            )
        )
    return output
