"""The core cache reports. The space sieve is applied in every one — a `NOT IN`
clause plus a post-filter — so a blocked space cannot reach a caller. SQL shapes
mirror the BI project's coord reader. (Assignee blocking and the assignee-name
glob's defence-in-depth live in the reader layer.)

WORLD_AS_OF (WORLD_AS_OF.design.md) is enforced here, inside the backend, not
at the front door: every dated read already carries an end-exclusive upper
bound (`create_time < %s` / `created_at < %s`), so the bound is one clamp on
`end` — and the cache->nocache fallback cannot leak, because each backend
bounds itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config, db, sieve

TASK_LIMIT = 1000
MESSAGE_LIMIT = 2000
# Freshness only polls the API for spaces active within this many days; a space
# dormant longer has no new messages worth a call (the live top-up's recency cut).
FRESH_HORIZON_DAYS = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def space_watermark(conn, space: str) -> datetime | None:
    """Newest cached message time for one space — the recency-gap lower bound the
    live top-up fetches above. None when the cache holds no message for it."""
    rows = db.query(
        conn,
        "SELECT MAX(create_time) AS wm FROM googlechat_messages WHERE space_name = %s",
        [space],
    )
    return rows[0]["wm"] if rows and rows[0]["wm"] else None


def active_spaces(conn, blocked: list[str], *, horizon_days: int = FRESH_HORIZON_DAYS) -> list[tuple[str, datetime]]:
    """Spaces with a cached message within `horizon_days`, each mapped to its newest
    cached message time, ranked most-recent first. The live top-up polls only these
    — a space dormant past the horizon has no new messages worth an API call, so
    this is the lever that keeps an unscoped freshness read within the read quota."""
    since = _utcnow() - timedelta(days=horizon_days)
    clause, sp = sieve.clause(blocked, "space_name")
    rows = db.query(
        conn,
        f"""
        SELECT space_name, MAX(create_time) AS wm
          FROM googlechat_messages
         WHERE create_time >= %s AND {clause}
         GROUP BY space_name
         ORDER BY wm DESC
        """,
        [since] + list(sp),
    )
    return [(r["space_name"], r["wm"]) for r in rows]


def _glob_to_like(pattern: str) -> str:
    """fnmatch-style glob -> SQL LIKE (with ESCAPE '\\'). Supports * and ?."""
    out = pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return out.replace("*", "%").replace("?", "_")


def spaces(conn, blocked: list[str], *, minimal_messages: int = 1) -> list[dict]:
    """Spaces with at least `minimal_messages` mirrored messages (default 1, so
    Google's auto-created empty meeting groups drop out; 0 shows them).

    Under WORLD_AS_OF the message and task count subqueries are bounded, which
    also makes the minimal_messages filter drop spaces whose first message
    postdates the cutoff — the proxy for "did not yet visibly exist". Space
    metadata (display name, type) is current-state and flagged, not rewound.
    """
    bound = config.world_as_of()
    msg_bound = " AND m.create_time < %s" if bound else ""
    task_bound = " AND t.created_at < %s" if bound else ""
    clause, params = sieve.clause(blocked, "s.name")
    rows = db.query(
        conn,
        f"""
        SELECT s.name AS space_name, s.display_name AS space_display, s.space_type,
               (SELECT COUNT(*) FROM googlechat_messages m WHERE m.space_name = s.name{msg_bound}) AS messages,
               (SELECT COUNT(*) FROM coord_tasks t WHERE t.space_name = s.name{task_bound}) AS tasks
          FROM googlechat_spaces s
         WHERE {clause}
           AND (SELECT COUNT(*) FROM googlechat_messages m WHERE m.space_name = s.name{msg_bound}) >= %s
         ORDER BY (s.display_name IS NULL), s.display_name, s.name
        """,
        ([bound, bound] if bound else []) + params + ([bound] if bound else []) + [minimal_messages],
    )
    return sieve.filter_rows(blocked, rows)


def people(conn, blocked: list[str], *, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """All participants (message senders union task assignees) over the window,
    with message and task counts. Display names come only from the prose @name
    on the task side (the mirror carries no user display names)."""
    end = config.world_clamp(end)
    clause, sp = sieve.clause(blocked, "space_name")
    wm, pm = [clause], list(sp)
    wt, pt = [clause], list(sp)
    if start:
        wm.append("create_time >= %s"); pm.append(start)
        wt.append("created_at >= %s"); pt.append(start)
    if end:
        wm.append("create_time < %s"); pm.append(end)
        wt.append("created_at < %s"); pt.append(end)
    rows = db.query(
        conn,
        f"""
        SELECT user_id, MAX(display) AS display, SUM(msgs) AS msgs, SUM(tasks) AS tasks
          FROM (
            SELECT sender_name AS user_id, NULL AS display, COUNT(*) AS msgs, 0 AS tasks
              FROM googlechat_messages
             WHERE sender_name IS NOT NULL AND {" AND ".join(wm)}
             GROUP BY sender_name
            UNION ALL
            SELECT assignee_user_name AS user_id, MAX(assignee_display) AS display, 0 AS msgs, COUNT(*) AS tasks
              FROM coord_tasks
             WHERE assignee_user_name IS NOT NULL AND {" AND ".join(wt)}
             GROUP BY assignee_user_name
          ) u
         GROUP BY user_id
         ORDER BY (SUM(msgs) + SUM(tasks)) DESC
        """,
        pm + pt,
    )
    return rows


def tasks(
    conn,
    blocked: list[str],
    *,
    to_user: str | None = None,
    by_user: str | None = None,
    assignee: str | None = None,
    assignee_name: str | None = None,
    space: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = TASK_LIMIT,
) -> list[dict]:
    end = config.world_clamp(end)
    where: list[str] = []
    params: list = []
    join = ""
    if by_user:
        join = "JOIN googlechat_messages m ON m.name = t.source_message_name"
        where.append("m.sender_name = %s")
        params.append(by_user)
    for col, val in (("t.assignee_user_name", to_user), ("t.assignee_user_name", assignee), ("t.space_name", space)):
        if val:
            where.append(f"{col} = %s")
            params.append(val)
    if assignee_name:
        # Backslash is MySQL's default LIKE escape, so _glob_to_like's \% \_ work.
        where.append("COALESCE(u.display_name, t.assignee_display) LIKE %s")
        params.append(_glob_to_like(assignee_name))
    if start:
        where.append("t.created_at >= %s")
        params.append(start)
    if end:
        where.append("t.created_at < %s")
        params.append(end)
    sclause, sparams = sieve.clause(blocked, "t.space_name")
    where.append(sclause)
    params.extend(sparams)
    rows = db.query(
        conn,
        f"""
        SELECT t.source_message_name, t.space_name, s.display_name AS space_display,
               t.assignee_user_name,
               COALESCE(u.display_name, t.assignee_display) AS assignee,
               t.title, t.created_at, t.status
          FROM coord_tasks t
          LEFT JOIN googlechat_spaces s ON s.name = t.space_name
          LEFT JOIN googlechat_users u ON u.name = t.assignee_user_name
          {join}
         WHERE {" AND ".join(where)}
         ORDER BY t.created_at DESC
         LIMIT %s
        """,
        params + [limit],
    )
    return sieve.filter_rows(blocked, rows)


def messages(
    conn,
    blocked: list[str],
    *,
    space: str | None = None,
    thread: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = MESSAGE_LIMIT,
) -> list[dict]:
    if not space and not thread:
        raise SystemExit("majordomo: messages needs --space or --thread.")
    if space and not sieve.allows(blocked, space):
        return []
    end = config.world_clamp(end)
    where: list[str] = []
    params: list = []
    if thread:
        where.append("m.name LIKE %s")
        params.append(thread.split(".")[0] + ".%")
    if space:
        where.append("m.space_name = %s")
        params.append(space)
    if start:
        where.append("m.create_time >= %s")
        params.append(start)
    if end:
        where.append("m.create_time < %s")
        params.append(end)
    sclause, sparams = sieve.clause(blocked, "m.space_name")
    where.append(sclause)
    params.extend(sparams)
    rows = db.query(
        conn,
        f"""
        SELECT m.name, m.space_name, s.display_name AS space_display,
               m.sender_name, m.sender_type, m.create_time, m.text
          FROM googlechat_messages m
          LEFT JOIN googlechat_spaces s ON s.name = m.space_name
         WHERE {" AND ".join(where)}
         ORDER BY m.create_time ASC
         LIMIT %s
        """,
        params + [limit],
    )
    return sieve.filter_rows(blocked, rows)
