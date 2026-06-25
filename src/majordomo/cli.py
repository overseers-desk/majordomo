"""majordomo — command-line front door. Thin: parse flags, call ``reports``,
render with ``output``. All behaviour lives in the core modules, so the later
MCP front door can call the same ``reports`` functions.
"""

from __future__ import annotations

from typing import Optional

import typer

from . import config, db, dates, models, reports
from .output import emit

app = typer.Typer(
    help="Read and report Google Chat task activity from the cache mirror.",
    no_args_is_help=True,
    add_completion=False,
)


def _open() -> tuple[dict, object, list[str]]:
    cfg = config.load_config()
    conn = db.connect()
    return cfg, conn, config.block_spaces(cfg)


def _warn_if_capped(rows: list, limit: int) -> None:
    if len(rows) >= limit:
        typer.echo(
            f"majordomo: capped at {limit} row(s); narrow --window/--space or raise --limit.",
            err=True,
        )


def _me(cfg: dict) -> str:
    uid = config.me_user_id(cfg)
    if uid:
        return uid
    email = config.me_google_id(cfg)
    hint = f" (config has [me].google_id={email!r}, an email, which v1 cannot resolve)" if email else ""
    typer.echo(
        f"majordomo: --to-me/--by-me need [me].user_id in config{hint}.\n"
        "Run `majordomo people` to find your users/<id>, then add it as [me].user_id.",
        err=True,
    )
    raise typer.Exit(2)


@app.command()
def spaces(json_out: bool = typer.Option(False, "--json", help="Raw JSON.")) -> None:
    """List spaces with their task counts."""
    _cfg, conn, blocked = _open()
    emit(reports.spaces(conn, blocked), models.SPACE_COLUMNS, models.SOURCE_CACHE, json_out)


@app.command()
def people(json_out: bool = typer.Option(False, "--json", help="Raw JSON.")) -> None:
    """List task assignees (id, name, count) — find your own users/<id> here."""
    _cfg, conn, blocked = _open()
    emit(reports.people(conn, blocked), models.PEOPLE_COLUMNS, models.SOURCE_CACHE, json_out)


@app.command()
def tasks(
    to_me: bool = typer.Option(False, "--to-me", help="Tasks assigned to you."),
    by_me: bool = typer.Option(False, "--by-me", help="Tasks you assigned."),
    assignee: Optional[str] = typer.Option(None, "--assignee", help="Tasks assigned to this users/<id>."),
    space: Optional[str] = typer.Option(None, "--space", help="Limit to this space (spaces/<id>)."),
    window: str = typer.Option("month", "--window", help="7d | 30d | month | all."),
    since: Optional[str] = typer.Option(None, "--since", help="ISO date lower bound (overrides window)."),
    until: Optional[str] = typer.Option(None, "--until", help="ISO date upper bound."),
    limit: int = typer.Option(reports.TASK_LIMIT, "--limit", help="Maximum rows."),
    json_out: bool = typer.Option(False, "--json", help="Raw JSON."),
) -> None:
    """Report tasks, filtered by assignee, space, and date."""
    cfg, conn, blocked = _open()
    start, end = dates.resolve(window, since, until)
    rows = reports.tasks(
        conn,
        blocked,
        to_user=_me(cfg) if to_me else None,
        by_user=_me(cfg) if by_me else None,
        assignee=assignee,
        space=space,
        start=start,
        end=end,
        limit=limit,
    )
    emit(rows, models.TASK_COLUMNS, models.SOURCE_CACHE, json_out)
    _warn_if_capped(rows, limit)


@app.command()
def messages(
    space: str = typer.Option(..., "--space", help="Space resource name (spaces/<id>)."),
    window: str = typer.Option("month", "--window", help="7d | 30d | month | all."),
    since: Optional[str] = typer.Option(None, "--since", help="ISO date lower bound (overrides window)."),
    until: Optional[str] = typer.Option(None, "--until", help="ISO date upper bound."),
    limit: int = typer.Option(reports.MESSAGE_LIMIT, "--limit", help="Maximum rows."),
    json_out: bool = typer.Option(False, "--json", help="Raw JSON."),
) -> None:
    """Report messages in a space over a date range."""
    _cfg, conn, blocked = _open()
    start, end = dates.resolve(window, since, until)
    rows = reports.messages(conn, blocked, space=space, start=start, end=end, limit=limit)
    emit(rows, models.MESSAGE_COLUMNS, models.SOURCE_CACHE, json_out)
    _warn_if_capped(rows, limit)
