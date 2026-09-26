"""发布包签名：规范化负载 -> sha256 摘要 -> HMAC-SHA256 签名。

签名只覆盖发布包内容（词条、例外、依据与生效时间等），发布后的废止动作
另以只追加事件记录，不回头改写签名负载。
"""

import hashlib
import hmac
import json
import os
from typing import Any


def canonical_payload(payload: Any) -> bytes:
    """以确定性方式序列化负载：键排序、无空白、非 ASCII 原文保留。"""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_payload(payload)).hexdigest()


def signing_secret() -> bytes:
    return os.getenv("RELEASE_SIGNING_SECRET", "dev-only-signing-secret").encode("utf-8")


def sign(payload: Any) -> tuple[str, str]:
    """返回 (负载摘要, 签名)。"""
    body = canonical_payload(payload)
    digest = hashlib.sha256(body).hexdigest()
    signature = hmac.new(signing_secret(), body, hashlib.sha256).hexdigest()
    return digest, signature


def verify(payload: Any, digest: str, signature: str) -> bool:
    expected_digest, expected_signature = sign(payload)
    return hmac.compare_digest(expected_digest, digest) and hmac.compare_digest(
        expected_signature, signature
    )
