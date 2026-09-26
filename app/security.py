"""签名与鉴权原语。

发布包采用规范化字节 + HMAC-SHA256：任何一方拿到包内容与签名都可用同一主密钥复核，
签名只覆盖不可变的包内容，生命周期状态（废止、取代）不进入签名。
"""

import hashlib
import hmac
import json
import os
from typing import Any


def server_secret() -> bytes:
    return os.getenv("SIGNING_SECRET", "dev-signing-secret").encode("utf-8")


def admin_key() -> str:
    return os.getenv("ADMIN_KEY", "dev-admin-key")


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """把发布包负载序列化为确定性字节：键排序、无空白、UTF-8。"""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign(data: bytes) -> str:
    return hmac.new(server_secret(), data, hashlib.sha256).hexdigest()


def verify(data: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(data), signature or "")


def unit_token(unit_id: str) -> str:
    """单位令牌与单位编号绑定，不含真实身份信息。"""
    return sign(f"unit-token:{unit_id}".encode("utf-8"))
