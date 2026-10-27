from pathlib import Path
from typing import Any

import yaml


def test_dependency_services_are_local_persistent_and_secrets_required() -> None:
    compose: dict[str, Any] = yaml.safe_load(
        (Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    )
    for service in compose["services"].values():
        assert all(port.startswith("127.0.0.1:") for port in service["ports"])
        assert service["volumes"]
        for name, value in service["environment"].items():
            if "PASSWORD" in name or name in ("REDISCLI_AUTH", "RABBITMQ_DEFAULT_PASS"):
                assert ":?" in value, f"{name}缺少拒绝默认凭据的条件"
    assert compose["networks"]["infra"]["driver_opts"] == {
        "com.docker.network.bridge.host_binding_ipv4": "127.0.0.1"
    }


def test_initialization_never_grants_privileged_runtime_role() -> None:
    sql = (Path(__file__).parents[2] / "deployment/postgres/init.sh").read_text(encoding="utf-8")
    for role in ("app_login", "app_auth"):
        create = next(line for line in sql.splitlines() if f"CREATE ROLE {role} " in line)
        assert "NOBYPASSRLS" in create and "NOSUPERUSER" in create
    assert "GRANT CREATE ON SCHEMA public TO app_login" not in sql
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in sql
