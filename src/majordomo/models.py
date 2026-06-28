"""The shapes majordomo reports, and how they render — in one place.

Each column spec is ``(header, key)`` where ``key`` is a row dict key or a
callable ``(row) -> value``. Keeping the specs here gives the task / space /
people / message shapes a single home. ``SOURCE_CACHE`` is the provenance tag
for rows read from the mirror.
"""

from __future__ import annotations

from collections.abc import Callable

SOURCE_CACHE = "cache"

Column = tuple[str, "str | Callable[[dict], object]"]


def _space_label(row: dict) -> object:
    return row.get("space_display") or row.get("space_name")


TASK_COLUMNS: list[Column] = [
    ("Created", "created_at"),
    ("Assignee", lambda r: r.get("assignee") or "(unassigned)"),
    ("Space", _space_label),
    ("Status", "status"),
    ("Title", lambda r: r.get("title") or ""),
]

SPACE_COLUMNS: list[Column] = [
    ("Space", _space_label),
    ("Type", "space_type"),
    ("Msgs", "messages"),
    ("Tasks", "tasks"),
    ("ID", "space_name"),
]

PEOPLE_COLUMNS: list[Column] = [
    ("Person", lambda r: r.get("display") or "(no name)"),
    ("Msgs", "msgs"),
    ("Tasks", "tasks"),
    ("User ID", "user_id"),
]

MESSAGE_COLUMNS: list[Column] = [
    ("Time", "create_time"),
    ("Sender", "sender_name"),
    ("Type", "sender_type"),
    ("Text", lambda r: (r.get("text") or "").replace("\n", " ")[:100]),
]
