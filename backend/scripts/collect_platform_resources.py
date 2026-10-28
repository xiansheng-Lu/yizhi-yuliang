"""从指定独立项目采集私有交接件；不复制令牌，不触网，不变更原项目。"""

import argparse
import json
from pathlib import Path

from yuliang.modules.connectors.resources import (
    checklist_projection,
    collect_resources,
    private_write,
    resource_summary,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adq-root", type=Path, required=True)
    parser.add_argument("--wxshop-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--distinct-shops", action="store_true")
    parser.add_argument("--write-checklist", action="store_true")
    args = parser.parse_args()
    bundle = collect_resources(args.adq_root, args.wxshop_root, distinct_shops=args.distinct_shops)
    private_write(args.output, bundle.model_dump(mode="json"))
    if args.write_checklist:
        template = Path(__file__).resolve().parents[2] / (
            "docs/backend/evidence/resource-handoff/platform-inputs.template.json"
        )
        private_write(
            args.output.with_name("inputs.local.json"),
            checklist_projection(bundle, json.loads(template.read_text(encoding="utf-8"))),
        )
    print(json.dumps(resource_summary(bundle), ensure_ascii=False))


if __name__ == "__main__":
    main()
