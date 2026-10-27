"""离线核对冻结内容、用户确认来源及预测；不调用模型或启用平台执行。"""

import argparse
import json
from pathlib import Path

from yuliang.modules.aikernel.prompts import load_bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--require-human-labels", action="store_true")
    parser.add_argument("--review", action="store_true", help="列出逐项签署所需的样例摘要")
    args = parser.parse_args()
    bundle = load_bundle()
    result = {
        "frozen_content": "PASS",
        "prompt_version": bundle.manifest.prompt_version,
        "status": bundle.manifest.status,
        "cases": len(bundle.cases),
        "pending_review_ids": bundle.pending_review_ids(),
        "confirmation_source": bundle.confirmation_source(),
        "confirmation_baseline": bundle.confirmation.baseline if bundle.confirmation else None,
        "template_sha256": bundle.manifest.template_sha256,
        "cases_sha256": bundle.manifest.cases_sha256,
        "release_allowed": False,
        "external_calls": 0,
    }
    if args.review:
        result["case_hashes"] = {case.id: case.case_hash() for case in bundle.cases}
    failed = False
    if args.predictions:
        comparison = bundle.compare(json.loads(args.predictions.read_text(encoding="utf-8")))
        result["comparison"] = comparison
        failed = bool(comparison["failed_ids"])
    if args.require_human_labels and bundle.pending_review_ids():
        failed = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
