"""微信豆目录及合成阶段证据不能解锁真实执行。"""

import pytest
from test_capability_evidence import NOW, proofs, target

from yuliang.modules.capability.models import aggregate


@pytest.mark.parametrize("kind", ["READ", "WRITE"])
def test_beans_cannot_be_enabled_by_complete_synthetic_evidence(kind: str) -> None:
    binding = target(kind=kind, connector="beans")
    result = aggregate(binding, proofs(binding), now=NOW)
    assert result["status"] == "NOT_INTEGRATED"
    assert result["operations"] == []
    assert result["validationReady"] is False


@pytest.mark.parametrize("kind", ["READ", "WRITE"])
def test_beans_without_official_source_remains_closed(kind: str) -> None:
    binding = target(kind=kind, connector="beans")
    result = aggregate(binding, [], now=NOW)
    assert result["status"] == "NOT_INTEGRATED"
    assert result["operations"] == []
    assert result["validationReady"] is False
