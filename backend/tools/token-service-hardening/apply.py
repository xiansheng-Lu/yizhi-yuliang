"""Verify or replay the independently maintained token-service fixes.

Usage: python apply.py tencent-adq D:/codex/tencent-adq [--apply]
Default is verification only. Refuses partially changed/incompatible sources.
Hashes are over UTF-8 text with normalized newlines; runtime files are untouched.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def digest(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()


def main():
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / "manifest.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", choices=manifest["projects"])
    parser.add_argument("target", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.target.resolve(strict=True)
    changes = manifest["projects"][args.project]
    actual = {item["path"]:digest(root / item["path"]) for item in changes}
    if all(actual[item["path"]] == item["after"] for item in changes):
        print("全部修复文件与已验收版本一致")
        return
    mismatches = [item["path"] for item in changes if actual[item["path"]] != item["before"]]
    if mismatches:
        raise SystemExit("源文件已变化或只应用了部分补丁，拒绝覆盖：" + ", ".join(mismatches))
    patch = here / (args.project + ".patch")
    command = ["git", "apply", "--ignore-space-change", "--whitespace=nowarn"]
    subprocess.run(command + ["--check", str(patch)], cwd=root, check=True)
    if not args.apply:
        print("基线与补丁检查通过；使用 --apply 才会修改源码")
        return
    subprocess.run(command + [str(patch)], cwd=root, check=True)
    if any(digest(root / item["path"]) != item["after"] for item in changes):
        raise SystemExit("应用后的文件摘要不匹配，请保留现场检查")
    print("补丁已应用且逐文件摘要校验通过")


if __name__ == "__main__":
    main()
