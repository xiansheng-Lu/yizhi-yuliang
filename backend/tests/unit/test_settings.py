import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from yuliang.main import create_app
from yuliang.settings import Settings


def test_missing_configuration_refuses_start_without_revealing_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)  # 不读取开发者本地.env。
    for key in list(__import__("os").environ):
        if key.startswith("YULIANG_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("YULIANG_SESSION_SECRET", "do-not-echo-this-secret")
    with pytest.raises(RuntimeError) as caught:
        create_app()
    assert "database_url" in str(caught.value)
    assert "do-not-echo" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_config_secrets_redacted_and_env_types_checked(settings: Settings) -> None:
    assert "test-only-secret" not in repr(settings)
    assert "app_login:example" not in repr(settings)
    values = settings.model_dump()
    values["session_secret"] = "short"
    with pytest.raises(ValidationError) as caught:
        Settings(**values)
    assert "short" not in str(caught.value)
    values["session_secret"] = settings.session_secret
    values["database_url"] = "sqlite:///local.db"
    with pytest.raises(ValidationError):
        Settings(**values)


def test_python_and_core_dependencies_import() -> None:
    assert sys.version_info[:2] == (3, 13)
    for module in ("fastapi", "pydantic", "sqlalchemy", "psycopg", "celery", "redis"):
        assert importlib.import_module(module)


def test_default_collection_excludes_live(tmp_path: Path) -> None:
    test_file = tmp_path / "test_live_sentinel.py"
    test_file.write_text(
        "import pytest\n@pytest.mark.live\ndef test_must_not_run():\n    assert False\n",
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603 -- 仅当前Python和受控临时测试文件。
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(Path(__file__).parents[2] / "pyproject.toml"),
            str(test_file),
            "--collect-only",
            "-q",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 5  # 全部被默认live过滤，不是执行成功。
    assert "deselected" in result.stdout
