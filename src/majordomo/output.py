"""Render report rows: a rich console table by default, raw JSON with ``--json``.

JSON wraps the rows in an envelope carrying the provenance ``source`` and a
``count``, so a machine reader can tell a cache answer from a direct (nocache) one.
Handed the WORLD_AS_OF bound as set (``world_as_of``), the envelope also
carries it and the current-state note, so a benchmark log proves the answer
was bounded; the console and CSV paths say the same once on stderr. Absent for
an unbounded run.
"""

from __future__ import annotations

import csv as _csv
import json
import sys

from . import config, dates
from .models import Column

from rich.console import Console
from rich.table import Table

console = Console()


def _cell(row: dict, key) -> str:
    value = dates.as_utc(key(row) if callable(key) else row.get(key))
    return "" if value is None else str(value)


def emit(rows: list[dict], columns: list[Column], source: str,
         output_json: bool = False, output_csv: bool = False, world_as_of: str | None = None) -> None:
    if output_json:
        envelope: dict = {"source": source, "count": len(rows), "rows": rows}
        if world_as_of:
            envelope["world_as_of"] = world_as_of
            envelope["current_state_note"] = config.WORLD_CURRENT_STATE_NOTE
        print(json.dumps(envelope, indent=2, default=lambda v: dates.as_utc(v).isoformat() if hasattr(v, "isoformat") else str(v)))
        return
    if world_as_of:
        print(
            f"majordomo: bounded to WORLD_AS_OF={world_as_of}; "
            f"{config.WORLD_CURRENT_STATE_NOTE}.",
            file=sys.stderr,
        )
    if output_csv:
        writer = _csv.writer(sys.stdout)
        writer.writerow([header for header, _ in columns])
        for row in rows:
            writer.writerow([_cell(row, key) for _, key in columns])
        return
    table = Table(show_header=True, header_style="bold cyan")
    for header, _ in columns:
        table.add_column(header)
    for row in rows:
        table.add_row(*[_cell(row, key) for _, key in columns])
    console.print(table)
    console.print(f"[dim]{len(rows)} row(s) · source: {source}[/dim]", highlight=False)
