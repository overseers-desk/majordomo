"""The sieve: the core privacy gate.

The sieve is a plain list of blocked space resource names (from
``[sieve].block_spaces``). These are the operations over it. Every report binds
``clause`` into its ``WHERE`` AND passes rows back through ``filter_rows`` as
defence in depth, so a blocked space cannot reach a caller through any front door.
"""

from __future__ import annotations


def clause(blocked: list[str], column: str = "space_name") -> tuple[str, list[str]]:
    """A SQL fragment plus its params, to AND into a WHERE. Empty-safe."""
    if not blocked:
        return ("1=1", [])
    marks = ",".join(["%s"] * len(blocked))
    return (f"{column} NOT IN ({marks})", list(blocked))


def filter_rows(blocked: list[str], rows: list[dict], key: str = "space_name") -> list[dict]:
    if not blocked:
        return rows
    return [r for r in rows if r.get(key) not in blocked]


def allows(blocked: list[str], space_name: str | None) -> bool:
    return space_name not in blocked


def filter_assignees(blocked: list[str], rows: list[dict],
                     id_key: str = "assignee_user_name", name_key: str = "assignee") -> list[dict]:
    """Drop rows whose assignee id or display name is in block_assignees (the
    IGNORE_ASSIGNEE half of the sieve). Matches on either the `users/<id>` or the
    prose @name, so a person can be blocked by whichever the config carries.
    """
    if not blocked:
        return rows
    blk = set(blocked)
    return [r for r in rows if r.get(id_key) not in blk and r.get(name_key) not in blk]
