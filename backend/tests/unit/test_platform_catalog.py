"""目录核对必须发现计数、来源、字段、ID/路径篡改，而非只验证JSON可解析。"""

import json
from pathlib import Path
from typing import Any

import pytest

from yuliang.modules.capability.catalog import json_object, load_catalog

ROOT = Path(__file__).resolve().parents[3]


def test_actual_catalog_has_full_counts_and_no_enabled_routes() -> None:
    bundle = load_catalog(ROOT)
    assert bundle.summary()["providers"] == {
        "tencent": 367,
        "ocean": 1070,
        "wxshop": 288,
        "xhs": 93,
    }
    assert len(bundle.records) == 1818 and bundle.empty_paths == 50
    assert bundle.duplicate_routes == 1 and bundle.summary()["enabledRoutes"] == 0
    assert len(bundle.sha256) == 64 and len(bundle.normalized_sha256) == 64


@pytest.mark.parametrize(
    "case",
    [
        "version",
        "source_hash",
        "source_count",
        "source_version",
        "source_path",
        "missing_record",
        "duplicate_id",
        "invented_method",
        "invented_path",
        "invented_scope",
        "invented_verification",
        "invented_provider",
        "empty_path_hidden",
    ],
)
def test_catalog_rejects_corruption_before_database(case: str) -> None:
    data: dict[str, Any] = json.loads(
        (ROOT / "frontend/src/data/platform-api-catalog.json").read_bytes()
    )
    if case == "version":
        data["schemaVersion"] = "unsupported"
    elif case.startswith("source_"):
        field, value = {
            "source_hash": ("sha256", "0" * 64),
            "source_count": ("recordCount", 1),
            "source_version": ("version", "unknown"),
            "source_path": ("file", "../../.env"),
        }[case]
        data["sources"][0][field] = value
    elif case == "missing_record":
        data["records"].pop()
    elif case == "duplicate_id":
        data["records"][1]["id"] = data["records"][0]["id"]
    elif case == "empty_path_hidden":
        next(row for row in data["records"] if not row["normalizedPath"])["normalizedPath"] = (
            "/fake"
        )
    else:
        field, value = {
            "invented_method": ("method", "POST"),
            "invented_path": ("normalizedPath", "/fake"),
            "invented_scope": ("scopes", ["fake"]),
            "invented_verification": ("pathEvidence", "SOURCE_VERIFIED"),
            "invented_provider": ("platform", "beans"),
        }[case]
        data["records"][0][field] = value
    with pytest.raises(ValueError):
        load_catalog(ROOT, raw=json.dumps(data, ensure_ascii=False).encode())


def test_duplicate_json_keys_and_non_object_are_rejected() -> None:
    for raw in ['{"sources":[],"sources":[]}', "[]", '{"nested":{"id":1,"id":2}}']:
        with pytest.raises(ValueError):
            json_object(raw)
