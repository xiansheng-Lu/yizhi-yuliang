"""分页游标与源范围绑定，未知密钥/替换/篡改关闭恢复。"""

import secrets
from uuid import uuid4

import pytest
from pydantic import SecretBytes, SecretStr

from yuliang.modules.connectors.sync_cursor import CursorCodec


def test_opaque_cursor_crypto_and_scope_binding() -> None:
    old, new = SecretBytes(secrets.token_bytes(32)), SecretBytes(secrets.token_bytes(32))
    audit = SecretStr(secrets.token_urlsafe(48))
    source = uuid4()
    codec = CursorCodec({1: old}, 1, audit)
    value = SecretStr("opaque-private-page")
    encrypted, version = codec.seal(1, source, value)
    assert encrypted is not None and encrypted != value.get_secret_value().encode()
    assert codec.open(1, source, encrypted, version) == value
    assert codec.digest(None) != codec.digest(SecretStr("null"))
    assert CursorCodec({1: old, 2: new}, 2, audit).open(1, source, encrypted, 1) == value
    for tenant, target, raw, key in [
        (2, source, encrypted, 1),
        (1, uuid4(), encrypted, 1),
        (1, source, encrypted, 2),
        (1, source, encrypted[:-1] + bytes([encrypted[-1] ^ 1]), 1),
    ]:
        with pytest.raises(ValueError, match="游标密文"):
            codec.open(tenant, target, raw, key)
    assert value.get_secret_value() not in repr(codec)
