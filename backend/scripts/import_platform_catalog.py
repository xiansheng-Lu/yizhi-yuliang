"""默认只核对固定仓库目录；--apply须显式提供独立迁移连接。"""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text

from yuliang.modules.capability.catalog import import_catalog, load_catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    bundle = load_catalog(Path(__file__).resolve().parents[2])
    if not args.apply:
        print(json.dumps(bundle.summary(), ensure_ascii=False))
        return 0
    url = os.environ.get("YULIANG_MIGRATION_DATABASE_URL")
    if not url:
        raise SystemExit("缺少独立迁移连接；不使用应用.env自动提权")
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE app_owner"))
            result = import_catalog(connection, bundle)
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
