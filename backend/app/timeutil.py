from datetime import datetime, timezone


def iso_utc(dt: datetime | None) -> str | None:
    """Aware datetime -> "2026-09-26T13:20:00Z" (the API's time format)."""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
