"""独立项目的私有资源交接；不导入可执行代码，不把缓存状态当已批准权限。"""

import json
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from yuliang.modules.connectors.external_tokens import ExternalTokenConfig


class ResourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class SecretReference(ResourceModel):
    file: Path
    key: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,79}$")

    def resolve(self) -> SecretStr:
        values = read_environment(self.file)
        if not values.get(self.key):
            raise ValueError("引用的本地凭据尚未提供")
        return SecretStr(values[self.key])


def read_environment(path: Path) -> dict[str, str]:
    """只读配置，不展开变量、不执行 shell、不改变当前进程环境。"""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


class ExternalTokenReference(ResourceModel):
    base_url: str
    api_key: SecretReference
    owner_confirmed: bool = False

    @model_validator(mode="after")
    def loopback_only(self) -> "ExternalTokenReference":
        # 校验URL不需要读取密钥，禁用配置不会触碰原项目凭据。
        ExternalTokenConfig(
            base_url=self.base_url, api_key=SecretStr("validation-only"), product="wxshop"
        )
        return self


class ApplicationResource(ResourceModel):
    id: UUID
    connector_id: Literal["adq", "wxshop"]
    app_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    mode: Literal["ADQ_OAUTH", "WXSHOP_SELF"]
    secret: SecretReference
    redirect_uri: str | None = None
    source_root: Path
    tenant_id: int | None = Field(default=None, strict=True, gt=0)
    shop_id: UUID | None = None
    account_id: UUID | None = None
    native_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    observed_native_ids: tuple[str, ...] = ()
    observed_scopes: tuple[str, ...] = ()
    granted_scopes: tuple[str, ...] | None = None
    permissions_evidence: str | None = None
    readonly_interfaces: tuple[str, ...] = ()
    token_owner_transferred: bool = False
    external_token_service: ExternalTokenReference | None = None
    enabled: bool = False

    @model_validator(mode="after")
    def coherent(self) -> "ApplicationResource":
        if (self.connector_id, self.mode) not in {("adq", "ADQ_OAUTH"), ("wxshop", "WXSHOP_SELF")}:
            raise ValueError("小店自研与ADQ OAuth必须使用各自授权模式")
        if self.external_token_service is not None and self.token_owner_transferred:
            raise ValueError("托管令牌与驭量自行刷新不能同时启用")
        owner_ready = (
            self.external_token_service.owner_confirmed
            if self.external_token_service is not None
            else self.token_owner_transferred
        )
        if self.enabled and (
            self.tenant_id is None
            or self.shop_id is None
            or self.native_id is None
            or self.granted_scopes is None
            or not self.permissions_evidence
            or not owner_ready
            or self.connector_id == "adq"
            and not self.redirect_uri
        ):
            raise ValueError("启用前需要真实映射、权限依据、固定回调和唯一刷新者交接")
        if any(not path.startswith("/") or "?" in path for path in self.readonly_interfaces):
            raise ValueError("只读接口必须是固定路径，不接收URL或认证参数")
        if (
            self.enabled
            and self.connector_id == "adq"
            and urlsplit(self.redirect_uri or "").path
            != (f"/v1/connections/oauth/{self.id}/callback")
        ):
            raise ValueError("ADQ必须登记驭量实际应用回调路由，不能沿用独立服务旧path")
        return self

    @property
    def provider(self) -> str:
        return "tencent" if self.connector_id == "adq" else "wxshop"


class ResourceBundle(ResourceModel):
    format: Literal["yuliang-platform-resources-v1"] = "yuliang-platform-resources-v1"
    applications: tuple[ApplicationResource, ...]
    keyring_file: Path | None = None
    state_secret_file: Path | None = None
    key_version: int = Field(default=1, strict=True, gt=0)
    shop_relationship: Literal["UNCONFIRMED", "DISTINCT_SHOPS"] = "UNCONFIRMED"

    @model_validator(mode="after")
    def unique(self) -> "ResourceBundle":
        ids = [app.id for app in self.applications]
        identities = [(app.tenant_id, app.connector_id, app.app_id) for app in self.applications]
        if len(set(ids)) != len(ids) or len(set(identities)) != len(identities):
            raise ValueError("资源应用重复，拒绝隐式覆盖")
        shops = [app.shop_id for app in self.applications if app.shop_id is not None]
        if self.shop_relationship == "DISTINCT_SHOPS" and len(set(shops)) != len(shops):
            raise ValueError("已确认属于不同店铺的资源不能绑定同一内部店铺")
        return self


def collect_resources(
    adq_root: Path, wxshop_root: Path, *, distinct_shops: bool = False
) -> ResourceBundle:
    """仅采集配置与历史元信息；不解密、不复制令牌，不产生远程调用。"""
    applications = []
    for connector, root, app_key, secret_key, mode in (
        ("adq", adq_root, "ADQ_CLIENT_ID", "ADQ_CLIENT_SECRET", "ADQ_OAUTH"),
        ("wxshop", wxshop_root, "WX_APPID", "WX_SECRET", "WXSHOP_SELF"),
    ):
        root = root.resolve(strict=True)
        values = read_environment(root / ".env")
        account_ids: list[str] = []
        scopes: set[str] = set()
        if connector == "adq":
            source = Path(values.get("ADQ_DATA_DIR") or root / "data")
            if not source.is_absolute():
                source = root / source
            credential_file = source / "adq_credentials.json"
            if credential_file.exists():
                raw = json.loads(credential_file.read_text(encoding="utf-8"))
                # 密文不经旧项目解密；历史明文只提取账号标识与scope，令牌不进入交接件。
                for native, record in raw.get("accounts", {}).items():
                    account_ids.append(str(native))
                    scopes.update(str(item) for item in record.get("scope_list", []))
        applications.append(
            ApplicationResource.model_validate(
                {
                    "id": uuid4(),
                    "connector_id": connector,
                    "mode": mode,
                    "app_id": values.get(app_key, ""),
                    "source_root": root,
                    "secret": {"file": root / ".env", "key": secret_key},
                    "redirect_uri": values.get("ADQ_REDIRECT_URI") if connector == "adq" else None,
                    "observed_native_ids": tuple(account_ids),
                    "observed_scopes": tuple(sorted(scopes)),
                }
            )
        )
    return ResourceBundle(
        applications=tuple(applications),
        shop_relationship="DISTINCT_SHOPS" if distinct_shops else "UNCONFIRMED",
    )


def private_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 不覆盖已经人工补齐的映射或权限材料。
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def resource_summary(bundle: ResourceBundle) -> dict[str, Any]:
    def available(app: ApplicationResource) -> bool:
        try:
            return bool(app.secret.resolve().get_secret_value())
        except (OSError, ValueError):
            return False

    return {
        "format": bundle.format,
        "containsSecrets": False,
        "shopRelationship": bundle.shop_relationship,
        "applications": [
            {
                "id": str(app.id),
                "connectorId": app.connector_id,
                "mode": app.mode,
                "localSecretReferencePresent": available(app),
                "observedAccountCount": len(app.observed_native_ids),
                "observedScopeCount": len(app.observed_scopes),
                "enabled": app.enabled,
                "mappingReady": app.tenant_id is not None and app.shop_id is not None,
                "permissionEvidenceReady": app.permissions_evidence is not None,
                "tokenOwnerTransferred": app.token_owner_transferred,
            }
            for app in bundle.applications
        ],
    }


def checklist_projection(bundle: ResourceBundle, template: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(template))
    result["observations"] = {
        "shopRelationship": bundle.shop_relationship,
        "source": "PREVIOUS_INDEPENDENT_TEST_PROJECTS",
        "businessTenantAssigned": False,
        "currentPlatformAuthorizationVerified": False,
    }
    for app in bundle.applications:
        item = result[app.connector_id]
        item["application"].update(
            {
                "appId": app.app_id,
                "environment": "PREVIOUS_TEST",
                "availableStatus": "LOCAL_REFERENCE_PRESENT_NOT_LIVE_VERIFIED",
                "secretMaterialRef": f"{app.secret.file}#{app.secret.key}",
            }
        )
        # 实现模式与后台实际批准模式分开；不把scope缓存回填成已批准权限。
        item["observations"] = {
            "implementedMode": app.mode,
            "configuredCallback": app.redirect_uri,
            "cachedScopes": list(app.observed_scopes),
        }
        if app.connector_id == "adq":
            shape = item["accountMappingItemShape"]
            item["accountMapping"] = [
                dict(
                    shape,
                    platformAccountId=native,
                    actualAuthorizationStatus="HISTORICAL_TEST_NOT_REVALIDATED",
                )
                for native in app.observed_native_ids
            ] or None
        else:
            item["shopMapping"] = [
                dict(
                    item["shopMappingItemShape"],
                    platformShopAppIdIfApplicable=app.app_id,
                    actualAuthorizationStatus="HISTORICAL_TEST_NOT_REVALIDATED",
                )
            ]
    return result
