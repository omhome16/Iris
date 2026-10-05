"""Time zones that work on Windows, where the system has no tz database.

`zoneinfo.ZoneInfo("UTC")` raises `ZoneInfoNotFoundError` on Windows unless the
`tzdata` package is installed. Every call site goes through here so a missing
database degrades to `datetime.timezone.utc` instead of failing the turn.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo


def try_zone(name: str | None) -> tzinfo | None:
    """The zone named `name`, or None when it is empty or unknown."""
    key = (name or "").strip()
    if not key:
        return None
    if key.upper() in {"UTC", "ETC/UTC", "GMT", "ETC/GMT"}:
        return UTC
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(key)
    except Exception:  # noqa: BLE001 - missing tzdata, or a name that is not a zone
        return None


def zone(name: str | None) -> tzinfo:
    """`name` when it exists, otherwise UTC. Never raises."""
    return try_zone(name) or UTC


def today(name: str | None = None):
    """The owner's local date. Day budgets and cron use this, not the server's date."""
    return now(name).date()


def now(name: str | None = None) -> datetime:
    """Aware 'now' in the owner's zone, or UTC when that zone is unknown."""
    from iris_ai.config import settings

    return datetime.now(zone(name if name is not None else settings.iris_timezone))
