# ruff: noqa: S101
"""检查R00冻结源码和路径矩阵；前端协议行为另用实际ApiClient校验。"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check() -> None:
    contracts = ROOT / "docs/backend/contracts"
    matrix = json.loads((contracts / "http-contracts.json").read_text(encoding="utf-8"))
    assert len(matrix["sourceSha256"]) >= 10, "未冻结关键源码"
    for name, expected in matrix["sourceSha256"].items():
        actual = hashlib.sha256(
            (ROOT / "frontend" / name).read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        assert actual == expected, f"前端已变化，需重新复核：{name}"
    all_routes = [tuple(route) for routes in matrix["routes"].values() for route in routes]
    assert len(all_routes) == len(set(all_routes))
    assert ("PATCH", "/v1/conversations/{conversationId}") in all_routes
    assert ("POST", "/v1/delivery/plan-changes") in all_routes
    assert ("POST", "/v1/delivery/configuration/materials") in all_routes
    samples = json.loads((contracts / "golden.json").read_text(encoding="utf-8"))
    assert samples["success"]["schemaVersion"] == matrix["outerSchemaVersion"]
    assert samples["logout"]["data"] is None
    assert samples["approval"]["decision"] == "APPROVE" and "comment" not in samples["approval"]
    assert len([v for v in samples["emptySnapshot"].values() if isinstance(v, list)]) == 12
    assert samples["identity"]["mode"] == "API"
    print(
        json.dumps(
            {
                "result": "PASS",
                "frozen_sources": len(matrix["sourceSha256"]),
                "routes": len(all_routes),
                "note": "静态规格，未代替数据库/平台验收",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    check()
