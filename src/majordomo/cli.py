"""majordomo — command-line front door. Thin: parse flags, pick a reader, render.

All behaviour lives in the core (readers / reports / decoder), so the MCP front
door calls the same readers. Source is cache by default (direct-API fallback if the
cache is down); `--cache` / `--live` (cache + freshness top-up) / `--nocache`
(direct API) select it. Output is console, `--json`, or `--csv`.
"""

from __future__ import annotations

import json
from typing import List, Optional

import typer

from . import _claude_command, config, dates, models, readers, version
from .output import emit


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"majordomo {version()}")
        raise typer.Exit()

# The office-wide replay bound, documented where flags are discovered.
_WORLD_EPILOG = (
    "WORLD_AS_OF (environment, ISO-8601 with timezone) bounds every answer to that "
    "instant when set: nothing dated after it is reported, and relative windows "
    "anchor to it instead of now. Unparseable or offset-less values are a hard error."
)

app = typer.Typer(
    help="Read Google Chat, report who holds which tasks, and send messages.",
    epilog=_WORLD_EPILOG,
    no_args_is_help=True,
    add_completion=False,
    # A crash must never dump locals: the login traceback would otherwise
    # print the OAuth client secret and authorization code in plaintext.
    pretty_exceptions_show_locals=False,
)

_WINDOW = "7d | 30d | month | year | all."


@app.callback()
def _root(
    ctx: typer.Context,
    live: bool = typer.Option(False, "--live", help="Also fetch messages newer than the cache holds (slower)."),
    nocache: bool = typer.Option(False, "--nocache", help="Bypass the cache; read the Chat API directly."),
    cache: bool = typer.Option(False, "--cache", help="Force the cache; fail if it is unreachable."),
    _version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    if sum((live, nocache, cache)) > 1:
        typer.echo("majordomo: use only one of --live / --nocache / --cache.", err=True)
        raise typer.Exit(2)
    ctx.obj = {"source": "live" if live else "nocache" if nocache else "cache" if cache else None}
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
    """Sign in to Google in your browser. Do this once before sending, or before reading with --live or --nocache."""
    from . import api
    path = api.login(config.load_config())
    typer.echo(f"majordomo: token written to {path}")


@app.command(epilog=_WORLD_EPILOG)
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


@app.command(epilog=_WORLD_EPILOG)
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


@app.command(epilog=_WORLD_EPILOG)
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


@app.command(epilog=_WORLD_EPILOG)
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


@app.command()
def send(
    text: Optional[str] = typer.Argument(None, help="Message text. Optional when --attach is given."),
    space: Optional[str] = typer.Option(None, "--space", help="Space resource name (spaces/<id>)."),
    thread: Optional[str] = typer.Option(None, "--thread", help="Reply in this thread (or any message name in it)."),
    to: Optional[str] = typer.Option(None, "--to", help="A person, by email or users/<id>: sends in your 1:1 DM, and says so if you have none with them."),
    attach: Optional[List[str]] = typer.Option(None, "--attach", help="A local file to attach; repeat for several. Message text becomes optional."),
    json_out: bool = typer.Option(False, "--json", help="Raw JSON of the created message."),
) -> None:
    """Send a Google Chat message to a space, a thread, or a person.

    Carries message text, one or more file attachments (--attach, repeatable),
    or both; at least one is required. An email address in --to names the
    person, not an email channel: the message arrives in your 1:1 Chat DM with
    them.

    Sending needs `majordomo login` first. Refused while WORLD_AS_OF is set.
    """
    if (space, thread, to).count(None) != 2:
        typer.echo("majordomo: send needs exactly one of --space / --thread / --to.", err=True)
        raise typer.Exit(2)
    from . import api
    cfg = config.load_config()
    created = api.send(cfg, config.block_spaces(cfg), space=space, thread=thread,
                       to=to, text=text, attachments=attach)
    if json_out:
        typer.echo(json.dumps(created, indent=2, default=str))
    else:
        typer.echo(f"majordomo: sent {created.get('name')}")


@app.command("install-claude-command")
def install_claude_command() -> None:
    """(Re)write the majordomo command into ~/.claude/commands/majordomo.md.

    Every run already keeps that file current silently; this writes it
    explicitly and reports where, for a first install or a forced refresh.
    """
    _claude_command.install(typer.echo)


@app.command()
def mcp() -> None:
    """Run the MCP server (stdio), so an AI agent can call majordomo as tools."""
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        typer.echo("majordomo: MCP support needs the extra — pip install 'majordomo[mcp]'.", err=True)
        raise typer.Exit(1)
    mcp_main()
