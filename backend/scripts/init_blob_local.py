"""仅本地开发MinIO初始化专用私有版本桶，禁止生产端点和通配CORS。"""

import argparse
from pathlib import Path
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", default="yuliang-private-local")
    parser.add_argument("--origin", action="append", default=[])
    args = parser.parse_args()
    if not args.bucket.startswith("yuliang-private-") or len(args.bucket) > 63:
        raise SystemExit("只能初始化yuliang-private-前缀本地专用桶")
    origins = args.origin
    for origin in origins:
        value = urlsplit(origin)
        if (
            value.hostname not in {"127.0.0.1", "localhost", "::1"}
            or value.scheme not in {"http", "https"}
            or value.path
            or value.query
            or value.fragment
            or value.username
        ):
            raise SystemExit("本地CORS必须为精确回环来源，不允许通配")
    root = Path(__file__).resolve().parents[1]
    values = dict(
        line.split("=", 1)
        for line in (root / "deployment/.env.infrastructure")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    )
    client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:19000",
        aws_access_key_id="yuliang-local",
        aws_secret_access_key=values["YULIANG_MINIO_PASSWORD"],
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, proxies={}),
    )
    try:
        try:
            client.head_bucket(Bucket=args.bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket"}:
                raise SystemExit("本地桶不可用") from None
            client.create_bucket(Bucket=args.bucket)
        try:
            policy = client.get_bucket_policy(Bucket=args.bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "NoSuchBucketPolicy":
                raise SystemExit("本地桶策略不可核对") from None
        else:
            if policy.get("Policy"):
                raise SystemExit("已有桶策略需单独复核，不自动覆盖")
        client.put_bucket_versioning(
            Bucket=args.bucket, VersioningConfiguration={"Status": "Enabled"}
        )
        if origins:
            try:
                client.put_bucket_cors(
                    Bucket=args.bucket,
                    CORSConfiguration={
                        "CORSRules": [
                            dict(
                                AllowedOrigins=origins,
                                AllowedMethods=["PUT", "GET", "HEAD"],
                                AllowedHeaders=[
                                    "content-type",
                                    "content-length",
                                    "x-amz-checksum-sha256",
                                    "x-amz-meta-upload-id",
                                ],
                                ExposeHeaders=["ETag", "x-amz-version-id", "x-amz-checksum-sha256"],
                                MaxAgeSeconds=300,
                            )
                        ]
                    },
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") == "NotImplemented":
                    raise SystemExit(
                        "私有版本桶已初始化；当前MinIO不支持逐桶CORS，需显式服务级来源配置后再做浏览器验收"
                    ) from None
                raise SystemExit("私有版本桶已初始化；精确来源CORS配置失败") from None
        print(
            "本地专用私有版本桶初始化完成；CORS仅在显式origin且实际支持时设置，浏览器接线另验；未改生产端点"
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
