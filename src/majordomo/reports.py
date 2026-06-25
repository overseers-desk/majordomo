"""The core reports over the cache. The sieve is applied in every one — both as a
``NOT IN`` clause and a post-filter — so a blocked space cannot reach a caller
through any front door. SQL shapes mirror the BI project's coord reader.
"""

from __future__ import annotations

from datetime import datetime

from . import db, sieve

TASK_LIMIT = 1000
MESSAGE_LIMIT = 2000


def spaces(conn, blocked: list[str]) -> list[dict]:
    clause, params = sieve.clause(blocked, "s.name")
    rows = db.query(
        conn,
        f"""
        SELECT s.name AS space_name, s.display_name AS space_display, s.space_type,
               (SELECT COUNT(*) FROM coord_tasks t WHERE t.space_name = s.name) AS tasks
          FROM googlechat_spaces s
         WHERE {clause}
         ORDER BY (s.display_name IS NULL), s.display_name, s.name
        """,
        params,
    )
    return sieve.filter_rows(blocked, rows)


def people(conn, blocked: list[str]) -> list[dict]:
    clause, params = sieve.clause(blocked, "space_name")
    return db.query(
        conn,
        f"""
        SELECT assignee_user_name AS user_id,
               MAX(assignee_display) AS display,
               COUNT(*) AS tasks
          FROM coord_tasks
         WHERE assignee_user_name IS NOT NULL AND {clause}
         GROUP BY assignee_user_name
         ORDER BY tasks DESC
        """,
        params,
    )


def tasks(
    conn,
    blocked: list[str],
    *,
    to_user: str | None = None,
    by_user: str | None = None,
    assignee: str | None = None,
    space: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = TASK_LIMIT,
) -> list[dict]:
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
    space: str,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = MESSAGE_LIMIT,
) -> list[dict]:
    if not sieve.allows(blocked, space):
        return []
    where = ["m.space_name = %s"]
    params: list = [space]
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
