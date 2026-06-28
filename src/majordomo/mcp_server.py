"""majordomo MCP server — the secondary front door (mirrors mailroom's shape).

Exposes the same reports as the CLI, over MCP, by going through the same reader
seam (`readers.make_reader`). The sieve is applied in the reader, so a tool
cannot bypass it. Each tool takes an optional `source` ("cache" | "live"); the
default is the cache fast path with a live fallback. Needs the `mcp` extra;
launched by `majordomo mcp` (stdio).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import config, dates, readers


def _jsonable(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        out.append({k: (v.isoformat() if isinstance(v, (datetime, date)) else v) for k, v in row.items()})
    return out


def _reader(source: Optional[str]):
    cfg = config.load_config()
    return cfg, readers.make_reader(cfg, source)


def _envelope(reader, rows: list[dict]) -> dict:
    return {"source": reader.source, "count": len(rows), "rows": _jsonable(rows)}


def create_server() -> FastMCP:
    server = FastMCP("majordomo")

    @server.tool()
    def spaces(source: Optional[str] = None) -> dict:
        """List Google Chat spaces with task counts. source: cache | live (default auto)."""
        _cfg, reader = _reader(source)
        return _envelope(reader, reader.spaces())

    @server.tool()
    def people(window: str = "year", since: Optional[str] = None, until: Optional[str] = None,
               source: Optional[str] = None) -> dict:
        """List participants (senders and assignees) with message and task counts.

        window is one of 7d, 30d, month, year, all; since/until are ISO dates.
        """
        _cfg, reader = _reader(source)
        start, end = dates.resolve(window, since, until)
        return _envelope(reader, reader.people(start=start, end=end))

    @server.tool()
    def tasks(
        to_me: bool = False,
        by_me: bool = False,
        assignee: Optional[str] = None,
        assignee_name: Optional[str] = None,
        space: Optional[str] = None,
        window: str = "month",
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = readers.reports.TASK_LIMIT,
        source: Optional[str] = None,
    ) -> dict:
        """Report tasks by assignee/space/date. to_me/by_me need [me].user_id.

        assignee_name is a glob over the prose @name; window is one of 7d, 30d,
        month, year, all; since/until are ISO dates. source: cache | live.
        """
        cfg, reader = _reader(source)
        me = config.require_user_id(cfg) if (to_me or by_me) else None
        start, end = dates.resolve(window, since, until)
        rows = reader.tasks(
            to_user=me if to_me else None,
            by_user=me if by_me else None,
            assignee=assignee,
            assignee_name=assignee_name,
            space=space,
            start=start,
            end=end,
            limit=limit,
        )
        return _envelope(reader, rows)

    @server.tool()
    def messages(
        space: Optional[str] = None,
        thread: Optional[str] = None,
        window: str = "month",
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = readers.reports.MESSAGE_LIMIT,
        source: Optional[str] = None,
    ) -> dict:
        """Report messages in a space or thread over a date range. Needs space or thread."""
        _cfg, reader = _reader(source)
        start, end = dates.resolve(window, since, until)
        return _envelope(reader, reader.messages(space, thread=thread, start=start, end=end, limit=limit))

    return server


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
