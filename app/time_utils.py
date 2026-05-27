from datetime import datetime, timezone


def now_utc() -> datetime:
    """Aware UTC datetime — replaces the deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """Coerce a possibly-naive datetime to aware UTC.
    Naive values are assumed to be UTC (historical SQLite storage convention)."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)
