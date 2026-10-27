"""生成独立本地随机配置；拒绝覆盖，不输出凭据。Python标准库即可运行。"""

import os
import secrets
from pathlib import Path


def prepare(root: Path) -> None:
    infrastructure = root / "deployment/.env.infrastructure"
    application = root / ".env"
    if infrastructure.exists() or application.exists():
        raise SystemExit("已有本地配置，请复用；不会覆盖或轮换既有凭据。")
    values = {
        name: secrets.token_hex(32)
        for name in ("POSTGRES", "APP", "AUTH", "MIGRATION", "REDIS", "BROKER", "MINIO")
    }
    app = {
        "YULIANG_ENVIRONMENT": "local",
        "YULIANG_DATABASE_URL": (
            f"postgresql+psycopg://app_login:{values['APP']}@127.0.0.1:15432/yizhi"
        ),
        "YULIANG_AUTH_DATABASE_URL": (
            f"postgresql+psycopg://app_auth:{values['AUTH']}@127.0.0.1:15432/yizhi"
        ),
        "YULIANG_REDIS_URL": f"redis://:{values['REDIS']}@127.0.0.1:16379/0",
        "YULIANG_BROKER_URL": f"amqp://yuliang:{values['BROKER']}@127.0.0.1:15672/yuliang",
        "YULIANG_SESSION_SECRET": secrets.token_hex(32),
        "YULIANG_ALLOWED_ORIGINS": '["http://localhost:3000"]',
    }
    infra = {f"YULIANG_{name}_PASSWORD": value for name, value in values.items()}
    for path, content in ((infrastructure, infra), (application, app)):
        # O_EXCL避免覆盖；WSL挂载盘权限还受Windows ACL约束。
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("".join(f"{key}={value}\n" for key, value in content.items()))
    print("本地配置已生成；密钥仅写入Git忽略文件。")


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[1])
