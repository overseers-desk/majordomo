"""The reader seam: one `Reader` interface, two interchangeable backends.

A second real backend (live Google Chat) now exists, so the readers are
polymorphic — every command and front door calls a `Reader` without branching on
source. The sieve is enforced inside each backend. `make_reader` selects the
backend and implements the provenance-tagged cache->live fallback: the output
always carries `source`, so a switch is surfaced, not silent.
"""

from __future__ import annotations

from . import config, db, reports

# A "reader" is any object with `source` and the four report methods
# (spaces / people / tasks / messages). CacheReader and live.LiveReader are the
# two; they are duck-typed, so no Protocol interface is declared.


class CacheReader:
    source = "cache"

    def __init__(self, conn, blocked: list[str]):
        self.conn = conn
        self.blocked = blocked

    def spaces(self) -> list[dict]:
        return reports.spaces(self.conn, self.blocked)

    def people(self) -> list[dict]:
        return reports.people(self.conn, self.blocked)

    def tasks(self, **filters) -> list[dict]:
        return reports.tasks(self.conn, self.blocked, **filters)

    def messages(self, space: str, **kw) -> list[dict]:
        return reports.messages(self.conn, self.blocked, space=space, **kw)


def make_reader(cfg: dict, source: str | None = None):
    """Pick a backend. `source` is "cache", "live", or None (auto).

    Auto reads the cache and falls back to live only if the DB is unreachable.
    Forced "cache" fails loud if the DB is down (no silent fallback). The fault
    a live switch catches — an absent backend — is real and expected, so this is
    not a phantom-problem fallback.
    """
    blocked = config.block_spaces(cfg)
    if source != "live":
        try:
            return CacheReader(db.connect(), blocked)
        except Exception:
            if source == "cache":
                raise
    from .live import LiveReader
    return LiveReader.from_config(cfg, blocked)
