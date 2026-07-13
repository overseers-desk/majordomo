"""Render report rows: a rich console table by default, raw JSON with ``--json``.

JSON wraps the rows in an envelope carrying the provenance ``source`` and a
``count``, so a machine reader can tell a cache answer from a direct (nocache) one.
Under WORLD_AS_OF the envelope also carries ``world_as_of`` (the bound as set,
so a benchmark log proves the answer was bounded) and the current-state note;
the console and CSV paths say the same once on stderr. Absent when unset.
"""

from __future__ import annotations

import csv as _csv
import json
import os
import sys

from . import config
from .models import Column

from rich.console import Console
from rich.table import Table

console = Console()


def _cell(row: dict, key) -> str:
    value = key(row) if callable(key) else row.get(key)
    return "" if value is None else str(value)


def emit(rows: list[dict], columns: list[Column], source: str,
         output_json: bool = False, output_csv: bool = False) -> None:
    bounded = os.environ.get(config.WORLD_AS_OF_ENV)
    if output_json:
        envelope: dict = {"source": source, "count": len(rows), "rows": rows}
        if bounded:
            envelope["world_as_of"] = bounded
            envelope["current_state_note"] = config.WORLD_CURRENT_STATE_NOTE
        print(json.dumps(envelope, indent=2, default=str))
        return
    if bounded:
        print(
            f"majordomo: bounded to WORLD_AS_OF={bounded}; "
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
