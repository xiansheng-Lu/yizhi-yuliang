"""AES-256-GCM，随机nonce和完整授权上下文AAD；旧版密钥仅保留解密。"""

import json
import secrets
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretBytes, SecretStr


class TokenCipherError(Exception):
    def __init__(self) -> None:
        super().__init__("令牌密钥或密文不可用")


@dataclass(frozen=True)
class TokenContext:
    tenant_id: int
    authorization_id: UUID
    application_id: UUID
    app_id: str
    connector_id: str
    provider: str
    product_line: str

    def aad(self, kind: Literal["access", "refresh"], version: int) -> bytes:
        return json.dumps(
            [
                "yuliang-token-v1",
                self.tenant_id,
                str(self.authorization_id),
                str(self.application_id),
                self.app_id,
                self.connector_id,
                self.provider,
                self.product_line,
                kind,
                version,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()


@dataclass(frozen=True)
class Ciphertext:
    key_version: int
    value: bytes = field(repr=False)


class TokenCipher:
    def __init__(self, keys: dict[int, SecretBytes], current_version: int) -> None:
        if (
            type(current_version) is not int
            or current_version not in keys
            or any(
                type(v) is not int or v < 1 or len(k.get_secret_value()) != 32
                for v, k in keys.items()
            )
        ):
            raise TokenCipherError()
        self._keys = MappingProxyType(dict(keys))
        self.current_version = current_version

    def seal(
        self, context: TokenContext, kind: Literal["access", "refresh"], token: SecretStr
    ) -> Ciphertext:
        try:
            raw = token.get_secret_value().encode()
        except UnicodeError:
            raise TokenCipherError() from None
        if not 1 <= len(raw) <= 16384:
            raise TokenCipherError()
        nonce = secrets.token_bytes(12)
        key = self._keys[self.current_version].get_secret_value()
        return Ciphertext(
            self.current_version,
            nonce + AESGCM(key).encrypt(nonce, raw, context.aad(kind, self.current_version)),
        )

    def open(
        self, context: TokenContext, kind: Literal["access", "refresh"], ciphertext: Ciphertext
    ) -> SecretStr:
        try:
            if not 29 <= len(ciphertext.value) <= 16412:
                raise ValueError("invalid envelope")
            key = self._keys[ciphertext.key_version].get_secret_value()
            nonce = ciphertext.value[:12]
            value = AESGCM(key).decrypt(
                nonce, ciphertext.value[12:], context.aad(kind, ciphertext.key_version)
            )
            return SecretStr(value.decode())
        except (KeyError, ValueError, InvalidTag, UnicodeError):
            raise TokenCipherError() from None
