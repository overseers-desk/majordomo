"""The reader seam: one `Reader` interface, two interchangeable backends.

A second real backend (the direct Chat API) now exists, so the readers are
polymorphic — every command and front door calls a `Reader` without branching on
source. The sieve is enforced inside each backend. `make_reader` selects the
backend and implements the provenance-tagged cache->nocache fallback: the output
always carries `source`, so a switch is surfaced, not silent.
"""

from __future__ import annotations

import sys
from datetime import datetime

from . import config, db, reports, sieve

# A "reader" is any object with `source` and the four report methods
# (spaces / people / tasks / messages). CacheReader and nocache.NocacheReader are
# the two; they are duck-typed, so no Protocol interface is declared. Both carry the
# space sieve and the block_assignees list and apply both.


class CacheReader:
    source = "cache"

    def __init__(self, conn, blocked: list[str], blocked_assignees: list[str] | None = None):
        self.conn = conn
        self.blocked = blocked
        self.blocked_assignees = blocked_assignees or []

    def spaces(self, minimal_messages: int = 1) -> list[dict]:
        return reports.spaces(self.conn, self.blocked, minimal_messages=minimal_messages)

    def people(self, **kw) -> list[dict]:
        rows = reports.people(self.conn, self.blocked, **kw)
        return sieve.filter_assignees(self.blocked_assignees, rows, id_key="user_id", name_key="display")

    def tasks(self, **filters) -> list[dict]:
        rows = reports.tasks(self.conn, self.blocked, **filters)
        return sieve.filter_assignees(self.blocked_assignees, rows)

    def messages(self, space: str | None = None, **kw) -> list[dict]:
        return reports.messages(self.conn, self.blocked, space=space, **kw)


class FreshReader:
    """The `--live` reader: up-to-dateness. Serves the cache (fast, bulk) and tops
    it up from the Chat API with records newer than each space's cache watermark, so
    the answer is current without re-reading what the mirror already holds. People
    and spaces come straight from cache (identities and membership are stable). The
    top-up polls one space when scoped, else only the recently-active spaces
    (reports.active_spaces) — the lever that keeps an unscoped read within quota.
    A third sibling of the CacheReader/NocacheReader duck-type, holding both.
    """

    source = "live"

    def __init__(self, cache: CacheReader, cfg: dict, blocked: list[str], blocked_assignees: list[str]):
        self.cache = cache
        self._cfg = cfg
        self.blocked = blocked
        self.blocked_assignees = blocked_assignees
        self._nc = None

    def _nocache(self):
        if self._nc is None:
            from .nocache import NocacheReader
            self._nc = NocacheReader.from_config(self._cfg, self.blocked, self.blocked_assignees)
        return self._nc

    # Stable dimensions never need a freshness fetch.
    def people(self, **kw) -> list[dict]:
        return self.cache.people(**kw)

    def spaces(self, minimal_messages: int = 1) -> list[dict]:
        return self.cache.spaces(minimal_messages=minimal_messages)

    @staticmethod
    def _api_start(watermark, start):
        # Fetch strictly above the later of (cache watermark, window start). The
        # cache covered [start, watermark] with `>=`; the API filter uses `>`, so
        # the boundary record is not double-counted.
        cand = [d for d in (watermark, start) if d]
        return max(cand) if cand else None

    @staticmethod
    def _merge(base: list[dict], fresh: list[dict], *, key: str, time_key: str, reverse: bool, limit: int) -> list[dict]:
        seen = {r.get(key) for r in base}
        merged = base + [r for r in fresh if r.get(key) not in seen]
        merged.sort(key=lambda r: r.get(time_key) or datetime.min, reverse=reverse)
        return merged[:limit]

    def _targets(self, space):
        if space:
            return [(space, reports.space_watermark(self.cache.conn, space))]
        return reports.active_spaces(self.cache.conn, self.blocked)

    @staticmethod
    def _bounded_targets(targets):
        """Under WORLD_AS_OF, drop top-up targets whose cache watermark is at or
        past the bound: the top-up fetches only records newer than the watermark,
        which the bound would exclude anyway, so the call is definitionally
        useless. `--live` degrades to the cache read plus a stderr note. A
        watermark short of the bound (a future bound, or the sync gap) keeps its
        top-up, the one case where `--live` still adds anything; the fetch
        itself is end-clamped inside NocacheReader.
        """
        bound = config.world_as_of()
        if bound is None:
            return list(targets)
        live = [(sp, wm) for sp, wm in targets if wm is None or wm < bound]
        if len(live) < len(targets):
            print(
                "majordomo: WORLD_AS_OF bound: --live top-up skipped where the "
                "cache already reaches the bound; served cache.",
                file=sys.stderr,
            )
        return live

    def tasks(self, *, to_user=None, by_user=None, assignee=None, assignee_name=None,
              space=None, start=None, end=None, limit=reports.TASK_LIMIT) -> list[dict]:
        base = self.cache.tasks(to_user=to_user, by_user=by_user, assignee=assignee,
                                assignee_name=assignee_name, space=space, start=start, end=end, limit=limit)
        targets = self._bounded_targets(self._targets(space))
        if not targets:
            return base
        nc = self._nocache()
        fresh: list[dict] = []
        for sp, wm in targets:
            fresh += nc.tasks(to_user=to_user, by_user=by_user, assignee=assignee,
                              assignee_name=assignee_name, space=sp,
                              start=self._api_start(wm, start), end=end, limit=limit)
        return self._merge(base, fresh, key="source_message_name", time_key="created_at", reverse=True, limit=limit)

    def messages(self, space: str | None = None, *, thread=None, start=None, end=None, limit=reports.MESSAGE_LIMIT) -> list[dict]:
        base = self.cache.messages(space, thread=thread, start=start, end=end, limit=limit)
        if space:
            targets = [(space, reports.space_watermark(self.cache.conn, space))]
        elif thread:
            from .nocache import _space_of
            sp = _space_of(thread.split(".")[0])
            targets = [(sp, reports.space_watermark(self.cache.conn, sp))] if sp else []
        else:
            return base  # reports.messages already required space or thread
        targets = self._bounded_targets(targets)
        if not targets:
            return base
        nc = self._nocache()
        fresh: list[dict] = []
        for sp, wm in targets:
            if not sp:
                continue
            fresh += nc.messages(space=sp, thread=thread, start=self._api_start(wm, start), end=end, limit=limit)
        return self._merge(base, fresh, key="name", time_key="create_time", reverse=False, limit=limit)


def make_reader(cfg: dict, source: str | None = None):
    """Pick a backend. `source` is "cache", "live", "nocache", or None (auto).

    - "nocache": read the Chat API directly, no cache.
    - "cache": cache only; fail loud if the DB is down (no silent fallback).
    - "live": up-to-dateness — cache base + a freshness top-up from the API.
    - None (auto): cache, falling back to the direct API only if the DB is down.

    cache/live/auto all need the DB; if it is unreachable, "cache" raises while
    "live"/auto degrade to the direct API (the only fresh source left). The fault
    that fallback catches — an absent backend — is real and expected, so this is
    not a phantom-problem fallback.
    """
    blocked = config.block_spaces(cfg)
    blocked_assignees = config.block_assignees(cfg)
    if source == "nocache":
        from .nocache import NocacheReader
        return NocacheReader.from_config(cfg, blocked, blocked_assignees)
    try:
        cache = CacheReader(db.connect(), blocked, blocked_assignees)
    except Exception:
        if source == "cache":
            raise
        from .nocache import NocacheReader
        return NocacheReader.from_config(cfg, blocked, blocked_assignees)
    if source == "live":
        return FreshReader(cache, cfg, blocked, blocked_assignees)
    return cache
