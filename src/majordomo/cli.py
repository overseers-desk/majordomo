"""majordomo — command-line front door. Thin: parse flags, pick a reader, render.

All behaviour lives in the core (readers / reports / decoder), so the MCP front
door calls the same readers. Source is cache by default with a live fallback;
`--cache` / `--live` force a backend. Output is console, `--json`, or `--csv`.
"""

from __future__ import annotations

from typing import Optional

import typer

from . import _claude_command, config, dates, models, readers
from .output import emit

app = typer.Typer(
    help="Read and report Google Chat task activity (cache fast path, live fallback).",
    no_args_is_help=True,
    add_completion=False,
    # A crash must never dump locals: the live-login traceback would otherwise
    # print the OAuth client secret and authorization code in plaintext.
    pretty_exceptions_show_locals=False,
)

_WINDOW = "7d | 30d | month | year | all."


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
    _claude_command.refresh()


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
def login() -> None:
    """Mint or refresh the live OAuth token via the browser (needs the `live` extra)."""
    from . import live
    path = live.login(config.load_config())
    typer.echo(f"majordomo: token written to {path}")


@app.command()
def spaces(
    ctx: typer.Context,
    minimal_messages: int = typer.Option(
        1, "--minimal-messages", help="Hide spaces with fewer than N messages (0 shows all)."),
    json_out: bool = typer.Option(False, "--json", help="Raw JSON."),
    csv_out: bool = typer.Option(False, "--csv", help="CSV to stdout."),
) -> None:
    """List spaces with their message and task counts."""
    _cfg, reader = _open(ctx)
    rows = reader.spaces(minimal_messages=minimal_messages)
    emit(rows, models.SPACE_COLUMNS, reader.source, json_out, csv_out)
    if reader.source == "cache" and minimal_messages > 0 and not (json_out or csv_out):
        typer.echo(
            f"majordomo: hiding spaces with < {minimal_messages} message(s) "
            "(Google auto-creates an empty group per meeting); --minimal-messages=0 shows all.",
            err=True,
        )


@app.command()
def people(
    ctx: typer.Context,
    window: str = typer.Option("year", "--window", help=_WINDOW),
    since: Optional[str] = typer.Option(None, "--since", help="ISO date lower bound (overrides window)."),
    until: Optional[str] = typer.Option(None, "--until", help="ISO date upper bound."),
    json_out: bool = typer.Option(False, "--json", help="Raw JSON."),
    csv_out: bool = typer.Option(False, "--csv", help="CSV to stdout."),
) -> None:
    """List participants (senders and assignees) with message and task counts."""
    _cfg, reader = _open(ctx)
    start, end = dates.resolve(window, since, until)
    emit(reader.people(start=start, end=end), models.PEOPLE_COLUMNS, reader.source, json_out, csv_out)


@app.command()
def tasks(
    ctx: typer.Context,
    to_me: bool = typer.Option(False, "--to-me", help="Tasks assigned to you."),
    by_me: bool = typer.Option(False, "--by-me", help="Tasks you assigned."),
    assignee: Optional[str] = typer.Option(None, "--assignee", help="Tasks assigned to this users/<id>."),
    assignee_name: Optional[str] = typer.Option(None, "--assignee-name", help="Assignee name glob, e.g. '*Alice*'."),
    space: Optional[str] = typer.Option(None, "--space", help="Limit to this space (spaces/<id>)."),
    window: str = typer.Option("month", "--window", help=_WINDOW),
    since: Optional[str] = typer.Option(None, "--since", help="ISO date lower bound (overrides window)."),
    until: Optional[str] = typer.Option(None, "--until", help="ISO date upper bound."),
    limit: int = typer.Option(readers.reports.TASK_LIMIT, "--limit", help="Maximum rows."),
    json_out: bool = typer.Option(False, "--json", help="Raw JSON."),
    csv_out: bool = typer.Option(False, "--csv", help="CSV to stdout."),
) -> None:
    """Report tasks, filtered by assignee, space, and date."""
    cfg, reader = _open(ctx)
    start, end = dates.resolve(window, since, until)
    rows = reader.tasks(
        to_user=_me(cfg) if to_me else None,
        by_user=_me(cfg) if by_me else None,
        assignee=assignee,
        assignee_name=assignee_name,
        space=space,
        start=start,
        end=end,
        limit=limit,
    )
    emit(rows, models.TASK_COLUMNS, reader.source, json_out, csv_out)
    _warn_if_capped(rows, limit)


@app.command()
def messages(
    ctx: typer.Context,
    space: Optional[str] = typer.Option(None, "--space", help="Space resource name (spaces/<id>)."),
    thread: Optional[str] = typer.Option(None, "--thread", help="A thread (or any message name in it)."),
    window: str = typer.Option("month", "--window", help=_WINDOW),
    since: Optional[str] = typer.Option(None, "--since", help="ISO date lower bound (overrides window)."),
    until: Optional[str] = typer.Option(None, "--until", help="ISO date upper bound."),
    limit: int = typer.Option(readers.reports.MESSAGE_LIMIT, "--limit", help="Maximum rows."),
    json_out: bool = typer.Option(False, "--json", help="Raw JSON."),
    csv_out: bool = typer.Option(False, "--csv", help="CSV to stdout."),
) -> None:
    """Report messages in a space or a thread over a date range."""
    _cfg, reader = _open(ctx)
    start, end = dates.resolve(window, since, until)
    rows = reader.messages(space, thread=thread, start=start, end=end, limit=limit)
    emit(rows, models.MESSAGE_COLUMNS, reader.source, json_out, csv_out)
    _warn_if_capped(rows, limit)


@app.command("install-claude-command")
def install_claude_command() -> None:
    """(Re)write the majordomo command into ~/.claude/commands/majordomo.md.

    Every run already keeps that file current silently; this writes it
    explicitly and reports where, for a first install or a forced refresh.
    """
    _claude_command.install(typer.echo)


@app.command()
def mcp() -> None:
    """Run the MCP server (stdio) — the secondary front door. Needs the `mcp` extra."""
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        typer.echo("majordomo: MCP support needs the extra — pip install 'majordomo[mcp]'.", err=True)
        raise typer.Exit(1)
    mcp_main()
