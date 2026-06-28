"""The reader seam: one `Reader` interface, two interchangeable backends.

A second real backend (live Google Chat) now exists, so the readers are
polymorphic — every command and front door calls a `Reader` without branching on
source. The sieve is enforced inside each backend. `make_reader` selects the
backend and implements the provenance-tagged cache->live fallback: the output
always carries `source`, so a switch is surfaced, not silent.
"""

from __future__ import annotations

from . import config, db, reports, sieve

# A "reader" is any object with `source` and the four report methods
# (spaces / people / tasks / messages). CacheReader and live.LiveReader are the
# two; they are duck-typed, so no Protocol interface is declared. Both carry the
# space sieve and the block_assignees list and apply both.


class CacheReader:
    source = "cache"

    def __init__(self, conn, blocked: list[str], blocked_assignees: list[str] | None = None):
        self.conn = conn
        self.blocked = blocked
        self.blocked_assignees = blocked_assignees or []

    def spaces(self) -> list[dict]:
        return reports.spaces(self.conn, self.blocked)

    def people(self, **kw) -> list[dict]:
        rows = reports.people(self.conn, self.blocked, **kw)
        return sieve.filter_assignees(self.blocked_assignees, rows, id_key="user_id", name_key="display")

    def tasks(self, **filters) -> list[dict]:
        rows = reports.tasks(self.conn, self.blocked, **filters)
        return sieve.filter_assignees(self.blocked_assignees, rows)

    def messages(self, space: str | None = None, **kw) -> list[dict]:
        return reports.messages(self.conn, self.blocked, space=space, **kw)


def make_reader(cfg: dict, source: str | None = None):
    """Pick a backend. `source` is "cache", "live", or None (auto).

    Auto reads the cache and falls back to live only if the DB is unreachable.
    Forced "cache" fails loud if the DB is down (no silent fallback). The fault
    a live switch catches — an absent backend — is real and expected, so this is
    not a phantom-problem fallback.
    """
    blocked = config.block_spaces(cfg)
    blocked_assignees = config.block_assignees(cfg)
    if source != "live":
        try:
            return CacheReader(db.connect(), blocked, blocked_assignees)
        except Exception:
            if source == "cache":
                raise
    from .live import LiveReader
    return LiveReader.from_config(cfg, blocked, blocked_assignees)
