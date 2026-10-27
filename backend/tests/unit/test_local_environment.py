import importlib.util
from pathlib import Path

import pytest


def test_local_configuration_is_random_separate_and_never_overwritten(tmp_path: Path) -> None:
    script = Path(__file__).parents[2] / "scripts/prepare_local.py"
    spec = importlib.util.spec_from_file_location("prepare_local", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "deployment").mkdir()
    module.prepare(tmp_path)
    infrastructure = tmp_path / "deployment/.env.infrastructure"
    before = infrastructure.read_bytes()
    secrets = [line.split("=", 1)[1] for line in before.decode().splitlines()]
    assert len(set(secrets)) == 7
    assert all(len(value) == 64 for value in secrets)
    app = (tmp_path / ".env").read_text(encoding="utf-8")
    assert secrets[1] in app and secrets[0] not in app and secrets[3] not in app
    with pytest.raises(SystemExit, match="不会覆盖"):
        module.prepare(tmp_path)
    assert infrastructure.read_bytes() == before
