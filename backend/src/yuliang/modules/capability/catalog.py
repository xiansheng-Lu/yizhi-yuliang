"""M1-01：从固定仓库资料核对并幂等导入描述性目录，不生成可执行路由。"""

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

SOURCE_FILES = {
    "tencent": ("腾讯营销开放平台API接口全景看板.html", "DATA", "Marketing API 3.0", 367),
    "ocean": ("巨量引擎开放平台API接口全景看板.html", "DATA", "图谱未统一标注版本", 1070),
    "wxshop": ("微信小店API全量接口图谱.html", "RAW", "2026-10-21", 288),
    "xhs": ("小红书开放平台API接口全景看板.html", "DATA", "聚光 Marketing API", 93),
}


@dataclass(frozen=True)
class CatalogBundle:
    sha256: str
    normalized_sha256: str
    sources: list[dict[str, Any]]
    records: list[dict[str, Any]]
    empty_paths: int
    duplicate_routes: int

    def summary(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "normalizedSha256": self.normalized_sha256,
            "schemaVersion": "platform-catalog-v1",
            "records": len(self.records),
            "providers": dict(Counter(record["platform"] for record in self.records)),
            "emptyPaths": self.empty_paths,
            "duplicateRouteGroups": self.duplicate_routes,
            "enabledRoutes": 0,
        }


def json_object(raw: str | bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("目录JSON含重复字段")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("目录JSON必须是对象")
    return value


def load_catalog(root: Path, *, raw: bytes | None = None) -> CatalogBundle:
    raw = raw if raw is not None else (root / "src/data/platform-api-catalog.json").read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("目录超出允许大小")
    data = json_object(raw)
    if data.get("schemaVersion") != "platform-catalog-v1":
        raise ValueError("目录版本不支持")
    reference = root / "docs/shared/reference/platform-api-reference"
    normalized_raw = (reference / "四平台开放API_规范化目录.json").read_bytes()
    normalized = json_object(normalized_raw)["interfaces"]
    sources, expected = [], []
    for provider, (filename, variable, version, count) in SOURCE_FILES.items():
        source = (reference / filename).read_text(encoding="utf-8").replace("\r\n", "\n")
        literal = re.search(rf"(?:const|var)\s+{variable}\s*=\s*(\{{[^\n]*\}});", source)
        if literal is None:
            raise ValueError("固定来源缺少JSON字面量")
        original = json_object(literal[1])
        rows = original.get("records", original.get("apis"))
        groups = original.get("domains", original.get("doms"))
        canon = [row for row in normalized if row["provider"] == provider]
        if not isinstance(rows, list) or len(rows) != count or len(canon) != count:
            raise ValueError("规范化目录与来源条数不一致")
        seen: Counter[str] = Counter()
        for row, item in zip(rows, canon, strict=True):
            path = str(row.get("p", row.get("path", ""))).replace("`", "").strip()
            expected_path = (
                "/v3.0/" + path
                if provider == "tencent"
                else "/api/open" + path
                if provider == "xhs" and path
                else path
            )
            if (item.get("path") or "") != expected_path:
                raise ValueError("规范化路径与原始来源不一致")
            group = groups[row["g"]].get("name") if groups and "g" in row else None
            identity = "|".join(
                str(part)
                for part in [
                    provider,
                    group or row.get("category", ""),
                    row.get("m", ""),
                    row.get("n", row.get("name", row.get("d", ""))),
                    path,
                ]
            )
            duplicate = seen[identity]
            seen[identity] += 1
            scope = item.get("scope")
            expected.append(
                {
                    "id": hashlib.sha256(f"{identity}|{duplicate}".encode()).hexdigest()[:20],
                    "platform": provider,
                    "domain": group or row.get("category", "未分类"),
                    "module": row.get("m", row.get("category", "未分类")),
                    "name": item.get("displayName")
                    or row.get("d")
                    or row.get("name")
                    or row.get("n")
                    or path,
                    "nativePath": path,
                    "normalizedPath": item.get("path"),
                    "method": item.get("method"),
                    "scopes": scope if isinstance(scope, list) else [str(scope)] if scope else [],
                    "pathEvidence": "SOURCE_VERIFIED"
                    if item.get("pathListedAsVerified") is True
                    else "LISTED"
                    if path
                    else "UNCONFIRMED",
                    "docUrl": row.get("docUrl"),
                    "sourceFile": filename,
                }
            )
        sources.append(
            {
                "platform": provider,
                "file": filename,
                "variable": variable,
                "version": version,
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
                "recordCount": count,
                "sourceVerifiedCount": sum(row.get("ok") == 1 for row in rows),
                "metadata": original.get("meta"),
            }
        )
    if data.get("sources") != sources or data.get("records") != expected:
        raise ValueError("导入目录与固定来源逐项核对不一致")
    ids = [record["id"] for record in expected]
    if len(set(ids)) != len(ids):
        raise ValueError("目录ID重复")
    routes = Counter(
        (record["platform"], record["method"], record["normalizedPath"])
        for record in expected
        if record["normalizedPath"]
    )
    return CatalogBundle(
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(normalized_raw).hexdigest(),
        sources,
        expected,
        sum(not record["normalizedPath"] for record in expected),
        sum(value > 1 for value in routes.values()),
    )


def import_catalog(connection: Connection, bundle: CatalogBundle) -> dict[str, Any]:
    """调用方持事务与专用维护身份；同一摘要串行导入，失败全部回滚。"""
    if connection.execute(text("SELECT current_user")).scalar_one() != "app_owner":
        raise ValueError("目录导入需要独立维护身份")
    connection.execute(text("SELECT pg_advisory_xact_lock(760101)"))
    existing = connection.execute(
        text("SELECT record_count FROM catalog_imports WHERE sha256=:sha"), {"sha": bundle.sha256}
    ).scalar_one_or_none()
    if existing is None:
        connection.execute(
            text("""
            INSERT INTO catalog_imports(sha256,schema_version,normalized_sha256,source_manifest,
                record_count,empty_paths,duplicate_routes)
            VALUES(:sha,'platform-catalog-v1',:normalized,CAST(:sources AS jsonb),
                :count,:empty,:dupes)
        """),
            {
                "sha": bundle.sha256,
                "normalized": bundle.normalized_sha256,
                "sources": json.dumps(bundle.sources, ensure_ascii=False),
                "count": len(bundle.records),
                "empty": bundle.empty_paths,
                "dupes": bundle.duplicate_routes,
            },
        )
        connection.execute(
            text("""
            INSERT INTO endpoint_catalog(catalog_sha256,catalog_id,provider,source_file,
                operation,normalized_path,http_method,scope_hint,doc_status,record_json)
            VALUES(:sha,:id,:platform,:sourceFile,:name,:normalizedPath,:method,
                CAST(:scopes AS jsonb),:pathEvidence,CAST(:record AS jsonb))
        """),
            [
                record
                | {
                    "sha": bundle.sha256,
                    "method": record["method"] or "UNKNOWN",
                    "normalizedPath": record["normalizedPath"] or None,
                    "scopes": json.dumps(record["scopes"]),
                    "record": json.dumps(record, ensure_ascii=False),
                }
                for record in bundle.records
            ],
        )
    actual = connection.execute(
        text("SELECT count(*) FROM endpoint_catalog WHERE catalog_sha256=:sha"),
        {"sha": bundle.sha256},
    ).scalar_one()
    if actual != len(bundle.records) or (existing is not None and existing != actual):
        raise ValueError("已存目录不完整，拒绝切换当前版本")
    connection.execute(
        text("""
        INSERT INTO catalog_head(singleton,catalog_sha256) VALUES(true,:sha)
        ON CONFLICT(singleton) DO UPDATE SET catalog_sha256=excluded.catalog_sha256
          WHERE catalog_head.catalog_sha256<>excluded.catalog_sha256
    """),
        {"sha": bundle.sha256},
    )
    return bundle.summary() | {"created": existing is None}


def connector_catalog(connection: Connection, connector_id: str) -> dict[str, Any]:
    row = (
        connection.execute(
            text(
                "SELECT connector_id,name,reference_provider,binding_status "
                "FROM catalog_connectors "
                "WHERE connector_id=:connector"
            ),
            {"connector": connector_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ValueError("连接器不存在或缺少已验证租户上下文")
    imported = (
        connection.execute(
            text("""
        SELECT i.sha256,i.schema_version,i.record_count,i.source_manifest,
          i.empty_paths,i.duplicate_routes FROM catalog_head h
          JOIN catalog_imports i ON i.sha256=h.catalog_sha256
    """)
        )
        .mappings()
        .first()
    )
    return dict(row) | {
        "catalog": dict(imported) if imported else None,
        "referenceOnly": True,
        "operations": [],
        "availability": "NOT_INTEGRATED",
        "note": "平台来源仅作资料索引；未核验连接器绑定、账户授权或可执行路由",
    }
