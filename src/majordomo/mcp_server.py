"""majordomo MCP server — the secondary front door (mirrors mailroom's shape).

Exposes the same reports as the CLI, over MCP, by calling the same ``reports``
core. The sieve is applied there, so a tool cannot bypass it any more than the
CLI can. Optional: needs the ``mcp`` extra (``pip install 'majordomo[mcp]'``).
Launched by ``majordomo mcp`` (stdio).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import config, db, dates, models, reports


def _jsonable(rows: list[dict]) -> list[dict]:
    """Flatten datetimes so the result serialises over MCP, matching --json."""
    out = []
    for row in rows:
        out.append({k: (v.isoformat() if isinstance(v, (datetime, date)) else v) for k, v in row.items()})
    return out


def _envelope(rows: list[dict]) -> dict:
    return {"source": models.SOURCE_CACHE, "count": len(rows), "rows": _jsonable(rows)}


def _open() -> tuple[dict, object, list[str]]:
    cfg = config.load_config()
    return cfg, db.connect(), config.block_spaces(cfg)


def create_server() -> FastMCP:
    server = FastMCP("majordomo")

    @server.tool()
    def spaces() -> dict:
        """List Google Chat spaces with their task counts (sieve applied)."""
        _cfg, conn, blocked = _open()
        return _envelope(reports.spaces(conn, blocked))

    @server.tool()
    def people() -> dict:
        """List task assignees: users/<id>, display name, and task count."""
        _cfg, conn, blocked = _open()
        return _envelope(reports.people(conn, blocked))

    @server.tool()
    def tasks(
        to_me: bool = False,
        by_me: bool = False,
        assignee: Optional[str] = None,
        space: Optional[str] = None,
        window: str = "month",
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = reports.TASK_LIMIT,
    ) -> dict:
        """Report tasks filtered by assignee/space/date. to_me/by_me need [me].user_id.

        window is one of 7d, 30d, month, all; since/until are ISO dates that
        override the window.
        """
        cfg, conn, blocked = _open()
        me = config.require_user_id(cfg) if (to_me or by_me) else None
        start, end = dates.resolve(window, since, until)
        rows = reports.tasks(
            conn,
            blocked,
            to_user=me if to_me else None,
            by_user=me if by_me else None,
            assignee=assignee,
            space=space,
            start=start,
            end=end,
            limit=limit,
        )
        return _envelope(rows)

    @server.tool()
    def messages(
        space: str,
        window: str = "month",
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = reports.MESSAGE_LIMIT,
    ) -> dict:
        """Report messages in a space over a date range."""
        _cfg, conn, blocked = _open()
        start, end = dates.resolve(window, since, until)
        return _envelope(reports.messages(conn, blocked, space=space, start=start, end=end, limit=limit))

    return server


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
