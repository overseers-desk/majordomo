"""Resolve a reporting window or an explicit range to (start, end) bounds.

Windows mirror the BI project's coord reader: ``7d``, ``30d``, ``month``
(the previous calendar month), and ``all``. An explicit ``--since`` / ``--until``
overrides the window. Bounds are naive UTC datetimes (or ``None`` for an open
end), matching ``coord_tasks.created_at`` / ``googlechat_messages.create_time``.

Under WORLD_AS_OF (WORLD_AS_OF.design.md) the bound is the clock: relative
windows anchor to it, not to now — ``7d`` is the seven days before the bound,
``month`` the calendar month before the one containing it — and ``end`` is
clamped down to it (a later user-supplied ``--until`` gets a stderr note).
Without the frozen now, a replayed ``--window 7d`` resolves against the replay
date and silently reports nothing.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from . import config

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
    bound = config.world_as_of()
    if since or until:
        start, end = _parse(since), _parse(until)
        if bound is None:
            return (start, end)
        if end is not None and end.tzinfo is not None:
            end = end.astimezone(timezone.utc).replace(tzinfo=None)
        if end is not None and end > bound:
            print(
                f"majordomo: --until {until!r} is after WORLD_AS_OF "
                f"({bound.isoformat()} UTC); clamped to the bound.",
                file=sys.stderr,
            )
        return (start, config.world_clamp(end))
    now = bound if bound is not None else _now_utc()
    if window == "all":
        start, end = None, None
    elif window == "7d":
        start, end = now - timedelta(days=7), None
    elif window == "30d":
        start, end = now - timedelta(days=30), None
    elif window == "year":
        start, end = now - timedelta(days=365), None
    elif window == "month":
        end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start = (end - timedelta(days=1)).replace(day=1)
    else:
        raise SystemExit(f"majordomo: unknown window {window!r}; use one of {', '.join(WINDOWS)}")
    # Identity when WORLD_AS_OF is unset; under a bound an open end closes at it.
    return (start, config.world_clamp(end))
