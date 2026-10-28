"""独立官方SDK只读补充目录；不替换冻结目录，不生成账户能力或写许可。"""

import hashlib
import json
from typing import Any

from sqlalchemy import Connection, text

from yuliang.modules.capability.route_sources import PROFILES, SDK_REVISION, ProfileName


def import_sdk_read_catalog(connection: Connection, profile: ProfileName) -> dict[str, str]:
    if connection.execute(text("SELECT current_user")).scalar_one() != "app_owner":
        raise ValueError("SDK来源导入需要独立维护身份")
    definition = PROFILES[profile]
    connection.execute(text("SELECT pg_advisory_xact_lock(760101)"))
    base = connection.execute(
        text("SELECT catalog_sha256 FROM catalog_head WHERE singleton")
    ).scalar_one()
    catalog_id = hashlib.sha256(f"official-sdk-read|{SDK_REVISION}|{profile}".encode()).hexdigest()[
        :20
    ]
    record: dict[str, Any] = dict(
        id=catalog_id,
        platform="tencent",
        name=definition.native_path,
        nativePath=definition.native_path,
        normalizedPath=definition.path,
        method="GET",
        scopes=[],
        pathEvidence="SOURCE_VERIFIED",
        sourceFile=definition.source_file,
        docUrl=f"https://github.com/TencentAd/marketing-api-go-sdk/blob/{SDK_REVISION}/"
        + definition.source_file,
        sdkRevision=SDK_REVISION,
        sourceSha256=definition.source_sha256,
        frontendExposed=False,
    )
    serialized = json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    normalized = hashlib.sha256(serialized.encode()).hexdigest()
    sha = hashlib.sha256(f"{base}|official-sdk-read-v1|{normalized}".encode()).hexdigest()
    connection.execute(
        text("""INSERT INTO catalog_imports(sha256,schema_version,normalized_sha256,
        source_manifest,record_count,empty_paths,duplicate_routes)
        VALUES(:sha,'official-sdk-read-catalog-v1',:normalized,CAST(:manifest AS jsonb),1,0,0)
        ON CONFLICT(sha256) DO NOTHING"""),
        dict(
            sha=sha,
            normalized=normalized,
            manifest=json.dumps(
                [
                    dict(
                        file=definition.source_file,
                        sha256=definition.source_sha256,
                        repository="https://github.com/TencentAd/marketing-api-go-sdk",
                        revision=SDK_REVISION,
                    )
                ]
            ),
        ),
    )
    connection.execute(
        text("""INSERT INTO endpoint_catalog(catalog_sha256,catalog_id,provider,
        source_file,operation,normalized_path,http_method,scope_hint,doc_status,record_json)
        VALUES(:sha,:id,'tencent',:file,:name,:path,'GET','[]','SOURCE_VERIFIED',
         CAST(:record AS jsonb))
        ON CONFLICT(catalog_sha256,catalog_id) DO NOTHING"""),
        dict(
            sha=sha,
            id=catalog_id,
            file=definition.source_file,
            name=definition.native_path,
            path=definition.path,
            record=serialized,
        ),
    )
    stored = connection.execute(
        text(
            "SELECT record_json FROM endpoint_catalog WHERE catalog_sha256=:sha AND catalog_id=:id"
        ),
        dict(sha=sha, id=catalog_id),
    ).scalar_one()
    if stored != record:
        raise ValueError("SDK来源同一摘要内容不一致")
    connection.execute(
        text("""INSERT INTO sdk_read_catalogs(catalog_sha256,base_catalog_sha256,
         profile,sdk_revision)
        VALUES(:sha,:base,:profile,:revision) ON CONFLICT(catalog_sha256) DO NOTHING"""),
        dict(sha=sha, base=base, profile=profile, revision=SDK_REVISION),
    )
    return dict(catalogSha256=sha, catalogId=catalog_id)
