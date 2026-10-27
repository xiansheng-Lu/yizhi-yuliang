"""不透明游标只加密持久化，HMAC用于比较，不在结果或异常中回显。"""

import json
import secrets
from types import MappingProxyType
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretBytes, SecretStr

from yuliang.modules.connectors.redaction import fingerprint


class CursorCodec:
    def __init__(
        self, keys: dict[int, SecretBytes], current_version: int, audit_key: SecretStr
    ) -> None:
        if (
            type(current_version) is not int
            or current_version not in keys
            or any(
                type(v) is not int or v < 1 or len(key.get_secret_value()) != 32
                for v, key in keys.items()
            )
        ):
            raise ValueError("游标密钥不可用")
        fingerprint(audit_key, "sync-key-check", b"")
        self._keys = MappingProxyType(dict(keys))
        self.version, self._audit = current_version, audit_key

    @staticmethod
    def _raw(cursor: SecretStr | None) -> bytes:
        if cursor is None:
            return b"null"
        try:
            value = cursor.get_secret_value()
            raw = json.dumps(value, ensure_ascii=True).encode()
        except (ValueError, UnicodeError):
            raise ValueError("游标不可用") from None
        if not value or len(raw) > 8192:
            raise ValueError("游标不可用")
        return raw

    def digest(self, cursor: SecretStr | None) -> str:
        return fingerprint(self._audit, "sync-cursor-v1", self._raw(cursor))

    def hash(self, purpose: str, value: bytes) -> str:
        return fingerprint(self._audit, "sync-" + purpose, value)

    def seal(
        self, tenant: int, source: UUID, cursor: SecretStr | None
    ) -> tuple[bytes | None, int | None]:
        if cursor is None:
            return None, None
        aad = f"yuliang-sync-cursor-v1|{tenant}|{source}|{self.version}".encode()
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self._keys[self.version].get_secret_value()).encrypt(
            nonce, self._raw(cursor), aad
        ), self.version

    def open(
        self, tenant: int, source: UUID, encrypted: bytes | None, version: int | None
    ) -> SecretStr | None:
        if encrypted is None and version is None:
            return None
        try:
            if encrypted is None or version is None or not 29 <= len(encrypted) <= 8220:
                raise ValueError("invalid envelope")
            aad = f"yuliang-sync-cursor-v1|{tenant}|{source}|{version}".encode()
            value = json.loads(
                AESGCM(self._keys[version].get_secret_value()).decrypt(
                    encrypted[:12], encrypted[12:], aad
                )
            )
            if not isinstance(value, str) or not value:
                raise ValueError("invalid cursor")
            return SecretStr(value)
        except (ValueError, InvalidTag, KeyError, UnicodeError):
            raise ValueError("游标密文或密钥不可用") from None
