"""majordomo — command-line front door. Thin: parse flags, pick a reader, render.

All behaviour lives in the core (readers / reports / decoder), so the MCP front
door calls the same readers. Source is cache by default with a live fallback;
`--cache` / `--live` force a backend. Every result is tagged with its source.
"""

from __future__ import annotations

from typing import Optional

import typer

from . import config, dates, models, readers
from .output import emit

app = typer.Typer(
    help="Read and report Google Chat task activity (cache fast path, live fallback).",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root(
    ctx: typer.Context,
    live: bool = typer.Option(False, "--live", help="Force the live Google read."),
    cache: bool = typer.Option(False, "--cache", help="Force the cache; fail if it is unreachable."),
) -> None:
    if live and cache:
        typer.echo("majordomo: use only one of --live / --cache.", err=True)
        raise typer.Exit(2)
    ctx.obj = {"source": "live" if live else "cache" if cache else None}


def _open(ctx: typer.Context):
    cfg = config.load_config()
    return cfg, readers.make_reader(cfg, ctx.obj["source"])


def _warn_if_capped(rows: list, limit: int) -> None:
    if len(rows) >= limit:
        typer.echo(
            f"majordomo: capped at {limit} row(s); narrow --window/--space or raise --limit.",
            err=True,
        )


def _me(cfg: dict) -> str:
    try:
        return config.require_user_id(cfg)
    except ValueError as exc:
        typer.echo(f"majordomo: {exc}", err=True)
        raise typer.Exit(2)


@app.command()
def spaces(ctx: typer.Context, json_out: bool = typer.Option(False, "--json", help="Raw JSON.")) -> None:
    """List spaces with their task counts."""
    _cfg, reader = _open(ctx)
    emit(reader.spaces(), models.SPACE_COLUMNS, reader.source, json_out)


@app.command()
def people(ctx: typer.Context, json_out: bool = typer.Option(False, "--json", help="Raw JSON.")) -> None:
    """List task assignees (id, name, count) — find your own users/<id> here."""
    _cfg, reader = _open(ctx)
    emit(reader.people(), models.PEOPLE_COLUMNS, reader.source, json_out)


@app.command()
def tasks(
    ctx: typer.Context,
    to_me: bool = typer.Option(False, "--to-me", help="Tasks assigned to you."),
    by_me: bool = typer.Option(False, "--by-me", help="Tasks you assigned."),
    assignee: Optional[str] = typer.Option(None, "--assignee", help="Tasks assigned to this users/<id>."),
    space: Optional[str] = typer.Option(None, "--space", help="Limit to this space (spaces/<id>)."),
    window: str = typer.Option("month", "--window", help="7d | 30d | month | all."),
    since: Optional[str] = typer.Option(None, "--since", help="ISO date lower bound (overrides window)."),
    until: Optional[str] = typer.Option(None, "--until", help="ISO date upper bound."),
    limit: int = typer.Option(readers.reports.TASK_LIMIT, "--limit", help="Maximum rows."),
    json_out: bool = typer.Option(False, "--json", help="Raw JSON."),
) -> None:
    """Report tasks, filtered by assignee, space, and date."""
    cfg, reader = _open(ctx)
    start, end = dates.resolve(window, since, until)
    rows = reader.tasks(
        to_user=_me(cfg) if to_me else None,
        by_user=_me(cfg) if by_me else None,
        assignee=assignee,
        space=space,
        start=start,
        end=end,
        limit=limit,
    )
    emit(rows, models.TASK_COLUMNS, reader.source, json_out)
    _warn_if_capped(rows, limit)


@app.command()
def messages(
    ctx: typer.Context,
    space: str = typer.Option(..., "--space", help="Space resource name (spaces/<id>)."),
    window: str = typer.Option("month", "--window", help="7d | 30d | month | all."),
    since: Optional[str] = typer.Option(None, "--since", help="ISO date lower bound (overrides window)."),
    until: Optional[str] = typer.Option(None, "--until", help="ISO date upper bound."),
    limit: int = typer.Option(readers.reports.MESSAGE_LIMIT, "--limit", help="Maximum rows."),
    json_out: bool = typer.Option(False, "--json", help="Raw JSON."),
) -> None:
    """Report messages in a space over a date range."""
    _cfg, reader = _open(ctx)
    start, end = dates.resolve(window, since, until)
    rows = reader.messages(space, start=start, end=end, limit=limit)
    emit(rows, models.MESSAGE_COLUMNS, reader.source, json_out)
    _warn_if_capped(rows, limit)


@app.command()
def mcp() -> None:
    """Run the MCP server (stdio) — the secondary front door. Needs the `mcp` extra."""
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        typer.echo("majordomo: MCP support needs the extra — pip install 'majordomo[mcp]'.", err=True)
        raise typer.Exit(1)
    mcp_main()
