"""时间工具：服务端时间可在测试中替换；全部以带偏移量的 ISO 8601 存储。"""

from datetime import datetime, timezone

UTC = timezone.utc


def now() -> datetime:
    """当前服务端时间（UTC，可被测试 monkeypatch 替换）。"""
    return datetime.now(UTC)


def now_iso() -> str:
    return to_iso(now())


def to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("时间必须带时区偏移量")
    return value.astimezone(UTC).isoformat()


def parse(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"时间缺少时区偏移量：{value}")
    return dt.astimezone(UTC)
