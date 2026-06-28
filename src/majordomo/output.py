"""Render report rows: a rich console table by default, raw JSON with ``--json``.

JSON wraps the rows in an envelope carrying the provenance ``source`` and a
``count``, so a machine reader can tell a cache answer from a future live one.
"""

from __future__ import annotations

import csv as _csv
import json
import sys

from rich.console import Console
from rich.table import Table

from .models import Column

console = Console()


def _cell(row: dict, key) -> str:
    value = key(row) if callable(key) else row.get(key)
    return "" if value is None else str(value)


def emit(rows: list[dict], columns: list[Column], source: str,
         output_json: bool = False, output_csv: bool = False) -> None:
    if output_json:
        print(json.dumps({"source": source, "count": len(rows), "rows": rows}, indent=2, default=str))
        return
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
