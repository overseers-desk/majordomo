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

def _domain_label(row: dict) -> object:
    # None means "not read over the API", not "unknown": cache rows carry no
    # such key at all (the mirror does not store it), so this renders blank
    # rather than claiming a domain or a consumer account either way.
    owned = row.get("domain_owned")
    return {True: "domain", False: "consumer"}.get(owned, "")


def _owner_label(row: dict) -> object:
    return row.get("owner_display") or row.get("owner_user_id") or ""


SPACE_COLUMNS: list[Column] = [
    ("Space", _space_label),
    ("Type", "space_type"),
    ("Msgs", "messages"),
    ("Tasks", "tasks"),
    ("Domain", _domain_label),
    ("Owner", _owner_label),
    ("ID", "space_name"),
]

# "Person" is the newest spelling seen; "Also" the older ones, so a rename
# reads as one person, not two.
PEOPLE_COLUMNS: list[Column] = [
    ("Person", lambda r: r.get("display") or "(no name)"),
    ("Also", lambda r: " | ".join((r.get("names") or [])[1:])),
    ("Email", "email"),
    ("DM", "dm_space"),
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

# "Saved" carries the path a download wrote, and renders empty on a plain
# listing, so listing and downloading report in one shape.
ATTACHMENT_COLUMNS: list[Column] = [
    ("Time", "create_time"),
    ("Sender", "sender_name"),
    ("File", lambda r: r.get("content_name") or ""),
    ("Type", "content_type"),
    ("Saved", "path"),
]
