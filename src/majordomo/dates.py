"""Resolve a reporting window or an explicit range to (start, end) bounds.

Windows mirror the BI project's coord reader: ``7d``, ``30d``, ``month``
(the previous calendar month), and ``all``. An explicit ``--since`` / ``--until``
overrides the window. Bounds are naive UTC datetimes (or ``None`` for an open
end), matching ``coord_tasks.created_at`` / ``googlechat_messages.create_time``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

WINDOWS = ("7d", "30d", "month", "year", "all")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"majordomo: bad date {value!r}; use YYYY-MM-DD")


def resolve(
    window: str = "month",
    since: str | None = None,
    until: str | None = None,
) -> tuple[datetime | None, datetime | None]:
    if since or until:
        return (_parse(since), _parse(until))
    now = _now_utc()
    if window == "all":
        return (None, None)
    if window == "7d":
        return (now - timedelta(days=7), None)
    if window == "30d":
        return (now - timedelta(days=30), None)
    if window == "year":
        return (now - timedelta(days=365), None)
    if window == "month":
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        first_prev = (first_this - timedelta(days=1)).replace(day=1)
        return (first_prev, first_this)
    raise SystemExit(f"majordomo: unknown window {window!r}; use one of {', '.join(WINDOWS)}")
