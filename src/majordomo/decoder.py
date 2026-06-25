"""Google Chat task decoder — Python port of the BI project's coord `decode.js`
(plus `jobs.js` title recovery). Pure: a Chat message -> a task record, or None.

Parity invariant (I5): `decode_task` produces the same fields as coord's
`decodeTask`, and `recover_titles` fills `title` the same way as `jobs.js`, so
the live path and the cache (`coord_tasks`) agree. Verified against the actual
JS in tests/test_decoder_parity.py; status is always "open" and is added by the
row layer, matching the `coord_tasks` default.
"""

from __future__ import annotations

import re
from datetime import datetime

VIA = "via Tasks"
_SPACE_RE = re.compile(r"^(spaces/[^/]+)/")


def space_of_message(message_name: str | None) -> str | None:
    m = _SPACE_RE.match(message_name or "")
    return m.group(1) if m else None


def is_task_creation(msg: dict | None) -> bool:
    text = (msg or {}).get("text") or ""
    return VIA in text and "Created" in text


def assignee_from_text(text: str | None) -> str | None:
    if not text or "@" not in text:
        return None
    name = text.split("@")[1].split("(")[0].strip()
    return name or None


def assignee_user_from_annotations(annotations) -> str | None:
    if not isinstance(annotations, list):
        return None
    for a in annotations:
        if isinstance(a, dict) and a.get("type") == "USER_MENTION":
            user = (a.get("userMention") or {}).get("user") or {}
            if user.get("name"):
                return user["name"]
    return None


def decode_task(msg: dict | None, space_name: str | None = None) -> dict | None:
    """A Chat message -> a task record (matching coord's `decodeTask`), or None.

    `title` is left None here; `recover_titles` fills it from the thread.
    """
    if not msg or not msg.get("name") or not is_task_creation(msg):
        return None
    text = msg.get("text") or ""
    return {
        "source_message_name": msg["name"],
        "space_name": space_name or space_of_message(msg["name"]),
        "assignee_user_name": assignee_user_from_annotations(msg.get("annotations")),
        "assignee_display": assignee_from_text(text),
        "title": None,
        "created_at": msg.get("createTime"),
    }


def _thread_key(message_name: str) -> str:
    """'spaces/X/messages/THREAD.MSG' -> 'spaces/X/messages/THREAD' (jobs.js)."""
    return message_name.split(".")[0]


def _ms(iso: str | None) -> float:
    if not iso:
        return float("-inf")
    try:
        return datetime.fromisoformat(iso).timestamp() * 1000
    except ValueError:
        return float("-inf")


def recover_titles(tasks: list[dict], messages: list[dict]) -> None:
    """Fill each task's `title` in place from the latest plain (non-`(via Tasks)`)
    message in its thread created before it (port of jobs.js). Best-effort: a
    source message outside the supplied `messages` leaves `title` None.
    """
    plain_by_thread: dict[str, list[dict]] = {}
    for m in messages:
        if "(via Tasks)" in (m.get("text") or "") or not m.get("name"):
            continue
        plain_by_thread.setdefault(_thread_key(m["name"]), []).append(m)
    for task in tasks:
        created = _ms(task.get("created_at"))
        best_text: str | None = None
        best_t = float("-inf")
        for p in plain_by_thread.get(_thread_key(task["source_message_name"]), []):
            pt = _ms(p.get("createTime"))
            if pt < created and pt > best_t:
                best_t = pt
                best_text = (p.get("text") or "").strip() or None
        if best_text is not None:
            task["title"] = best_text
