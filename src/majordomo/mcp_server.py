"""majordomo MCP server — the secondary front door (mirrors mailroom's shape).

Exposes the same reports as the CLI, over MCP, by going through the same reader
seam (`readers.make_reader`). The sieve is applied in the reader, so a tool
cannot bypass it. Each tool takes an optional `source` ("cache" | "live" | "nocache");
the default is the cache fast path with a direct-API fallback. Needs the `mcp` extra;
launched by `majordomo mcp` (stdio).
"""

from __future__ import annotations

import os
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
    try:
        cfg = config.load_config()
    except SystemExit as exc:
        # A config-time hard failure (a bad WORLD_AS_OF, a missing config) must
        # fail this tool call with its message, not kill the long-running server.
        # The bound is parsed per call, so the server honors the environment its
        # launcher set rather than dying opaquely at handshake.
        raise RuntimeError(str(exc)) from None
    return cfg, readers.make_reader(cfg, source)


def _envelope(reader, rows: list[dict]) -> dict:
    out = {"source": reader.source, "count": len(rows), "rows": _jsonable(rows)}
    bounded = os.environ.get(config.WORLD_AS_OF_ENV)
    if bounded:
        # Auditability: a bounded answer says so, so a benchmark log proves it.
        out["world_as_of"] = bounded
        out["current_state_note"] = config.WORLD_CURRENT_STATE_NOTE
    return out


def create_server() -> FastMCP:
    server = FastMCP("majordomo")

    @server.tool()
    def spaces(minimal_messages: int = 1, source: Optional[str] = None) -> dict:
        """List spaces with message and task counts. minimal_messages hides spaces
        with fewer than N messages (0 shows all; cache only). source: cache | live | nocache."""
        _cfg, reader = _reader(source)
        return _envelope(reader, reader.spaces(minimal_messages=minimal_messages))

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
        month, year, all; since/until are ISO dates. source: cache | live | nocache.
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
