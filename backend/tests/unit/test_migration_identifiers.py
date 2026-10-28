"""Fail fast before PG setup when revision metadata cannot fit or resolve."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_revision_identifiers_fit_version_table_and_resolve_parents() -> None:
    directory = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    revisions: dict[str, object] = {}
    for path in sorted(directory.glob("*.py")):
        values = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in ast.parse(path.read_text(encoding="utf-8-sig")).body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"revision", "down_revision"}
        }
        if "revision" not in values:
            continue
        revision = values["revision"]
        assert isinstance(revision, str) and 0 < len(revision) <= 32, path.name
        assert revision not in revisions, f"Duplicate revision in {path.name}"
        revisions[revision] = values.get("down_revision")

    assert revisions
    for revision, parent in revisions.items():
        parents = parent if isinstance(parent, tuple) else (parent,)
        assert all(value is None or value in revisions for value in parents), revision
