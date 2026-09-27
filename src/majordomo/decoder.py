"""Google Chat task decoder: the Python port of the BI project's coord
`decode.js`, with the title recovery and lifecycle replay of its `jobs.js`.
Pure: Chat messages in, task records out, nothing fetched.

Google's Tasks integration posts a message for each step of a task's life
(creation, the status verbs, the holder verbs: the tables below), none naming
a task id, so the thread is the only join: in a thread holding exactly one
task the events replay onto it, and a thread holding several is left alone, an
open default being better than a guess. Every verb anchors at the start of the
text with the "via Tasks" marker present, and an event is believed on its text
alone, as the cache believes it (Chat attributes these posts to the acting
human, so a hand-typed one is indistinguishable). The nocache path and the
cache (`coord_tasks`) thereby agree; tests/test_decoder_parity.py runs the JS.
"""

from __future__ import annotations

import re
from datetime import datetime

VIA = "via Tasks"
_SPACE_RE = re.compile(r"^(spaces/[^/]+)/")
_LIFECYCLE_VERBS = [("Completed a task", "completed"), ("Re-opened a task", "reopened"),
                    ("Deleted a task", "deleted"), ("Restored a task", "restored")]
# What each status verb leaves the task at: restored is delete's undo, back to
# open, since Chat itself forgets the pre-delete completion at restore.
_LIFECYCLE_STATUS = {"completed": "done", "reopened": "open", "deleted": "deleted", "restored": "open"}
_ASSIGN_VERBS = [("Assigned a task to ", "assigned"), ("Changed task assignee from ", "changed"),
                 ("Unassigned a task from ", "unassigned")]


def space_of_message(message_name: str | None) -> str | None:
    m = _SPACE_RE.match(message_name or "")
    return m.group(1) if m else None


def is_task_creation(msg: dict | None) -> bool:
    text = (msg or {}).get("text") or ""
    return VIA in text and text.startswith("Created a task")


def lifecycle_of(msg: dict | None) -> str | None:
    """Which status event after creation this message is, or None."""
    text = (msg or {}).get("text") or ""
    if VIA not in text:
        return None
    return next((kind for verb, kind in _LIFECYCLE_VERBS if text.startswith(verb)), None)


def assignee_change_of(msg: dict | None) -> dict | None:
    """Which holder event this message is, with the new holder: the last
    mention (a changed-assignee text carries two, the earlier one the previous
    holder) and the last @name. An unassignment leaves both None."""
    text = (msg or {}).get("text") or ""
    if VIA not in text:
        return None
    for verb, kind in _ASSIGN_VERBS:
        if text.startswith(verb):
            if kind == "unassigned":
                return {"kind": kind, "user": None, "display": None}
            return {"kind": kind, "user": assignee_user_from_annotations(msg.get("annotations"), last=True),
                    "display": assignee_from_text(text, last=True)}
    return None


def assignee_from_text(text: str | None, *, last: bool = False) -> str | None:
    """The @name in a task message, the first or with ``last`` the final one (a
    changed-assignee text carries two, the earlier being the previous holder),
    cut at the "(" that opens a marker such as "(P)" or "(via Tasks)"."""
    if not text or "@" not in text:
        return None
    name = text.split("@")[-1 if last else 1].split("(")[0].strip()
    return name or None


def assignee_user_from_annotations(annotations, *, last: bool = False) -> str | None:
    """The mentioned ``users/<id>``: the first mention, or with ``last`` the
    one at the greatest startIndex."""
    if not isinstance(annotations, list):
        return None
    best, best_idx = None, -1
    for a in annotations:
        if not isinstance(a, dict) or a.get("type") != "USER_MENTION":
            continue
        name = ((a.get("userMention") or {}).get("user") or {}).get("name")
        idx = a.get("startIndex") or 0
        if name and (best is None or (last and idx >= best_idx)):
            best, best_idx = name, idx
    return best


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


def replay_lifecycle(kinds) -> str | None:
    """The status a time-ordered run of status kinds leaves a task at, the last
    event winning, or None when nothing touched it."""
    status = None
    for k in kinds:
        status = _LIFECYCLE_STATUS.get(k, status)
    return status


def apply_lifecycle(tasks: list[dict], messages: list[dict]) -> None:
    """Replay each thread's status and holder events onto its task, in place
    (the reconstruction pass of jobs.js). Every task leaves with a ``status``:
    the replayed one, else "open"; in a single-task thread the last holder
    event also replaces the creation-time assignee."""
    by_thread: dict[str, list[dict]] = {}
    for t in tasks:
        t["status"] = "open"
        by_thread.setdefault(_thread_key(t["source_message_name"]), []).append(t)
    events: dict[str, list[tuple]] = {}
    for m in messages:
        status, assign = lifecycle_of(m), assignee_change_of(m)
        if m.get("name") and (status or assign):
            events.setdefault(_thread_key(m["name"]), []).append((_ms(m.get("createTime")), status, assign))
    for th, evs in events.items():
        held = by_thread.get(th)
        if not held or len(held) > 1:
            continue
        evs.sort(key=lambda e: e[0])
        held[0]["status"] = replay_lifecycle(s for _, s, _ in evs if s) or "open"
        last = next((a for _, _, a in reversed(evs) if a), None)
        if last:
            held[0]["assignee_user_name"], held[0]["assignee_display"] = last["user"], last["display"]
