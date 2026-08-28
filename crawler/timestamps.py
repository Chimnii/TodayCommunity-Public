from __future__ import annotations

from datetime import datetime, timezone


SOURCE_TIME_BASIS = "source"
FIRST_SEEN_TIME_BASIS = "first_seen"
SECOND_PRECISION = "second"
MINUTE_PRECISION = "minute"
DATE_PRECISION = "date"
TIME_BASES = frozenset({SOURCE_TIME_BASIS, FIRST_SEEN_TIME_BASIS})
TIME_PRECISIONS = frozenset({SECOND_PRECISION, MINUTE_PRECISION, DATE_PRECISION})


def canonical_utc(value: datetime) -> str:
    """Return one fixed-width UTC RFC 3339 timestamp used by persisted data."""

    if not isinstance(value, datetime):
        raise TypeError("canonical_utc requires a datetime value")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("canonical_utc requires a timezone-aware datetime")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def canonicalize_utc_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    normalized = value.strip()
    candidate = normalized[:-1] + "+00:00" if normalized.endswith("Z") else normalized
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    return canonical_utc(parsed)


def utc_now() -> str:
    return canonical_utc(datetime.now(timezone.utc))
