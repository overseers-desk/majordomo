"""The reader seam: one `Reader` interface, two interchangeable backends.

A second real backend (the direct Chat API) now exists, so the readers are
polymorphic — every command and front door calls a `Reader` without branching on
source. The sieve is enforced inside each backend. `make_reader` selects the
backend and implements the provenance-tagged cache->nocache fallback: the output
always carries `source`, so a switch is surfaced, not silent.

A person or a space reaches a reader as the caller typed it (a ``users/<id>``,
an email or a name; a ``spaces/<id>`` or a display name) and is resolved here,
inside the core, through known.py: both front doors pass the string through.
Every read also teaches known.py what it surfaced.
"""

from __future__ import annotations

import sys
from datetime import datetime

from . import config, db, known, reports, sieve

# A "reader" is any object with `source` and the four report methods
# (spaces / people / tasks / messages). CacheReader and api.NocacheReader are
# the two; they are duck-typed, so no Protocol interface is declared. Both carry the
# space sieve and the block_assignees list and apply both.


def decorate_people(kn: known.Known, rows: list[dict], person: str | None) -> list[dict]:
    """Give each people row its spellings, email and DM space from known.py,
    and narrow to one person when asked. The identity columns are not
    windowed: a person with no activity in the window still has a row."""
    if person:
        rows = [r for r in rows if r["user_id"] == person] or [
            {"user_id": person, "display": None, "msgs": 0, "tasks": 0}]
    for r in rows:
        names = kn.names_of(r["user_id"])
        if r.get("display") and r["display"] not in names:
            names.append(r["display"])
        r["display"] = names[0] if names else None
        r["names"] = names
        r["email"] = kn.email_of(r["user_id"])
        r["dm_space"] = kn.dm_space_of(r["user_id"])
    return rows


class CacheReader:
    source = "cache"

    def __init__(self, conn, blocked: list[str], blocked_assignees: list[str] | None = None,
                 cfg: dict | None = None, kn: known.Known | None = None):
        self.conn = conn
        self.blocked = blocked
        self.blocked_assignees = blocked_assignees or []
        self._cfg = cfg or {}
        self.known = kn or known.Known()

    # --- what the mirror teaches known.py --------------------------------

    def _seed_names(self) -> None:
        for r in reports.mention_rows(self.conn, self.blocked):
            self.known.learn_mentions(r["text"], r["annotations_json"], r["create_time"])
        for r in reports.task_names(self.conn, self.blocked):
            self.known.remember(r["user_id"], known.NAME, r["display"], r["first_seen"], "task")
            self.known.remember(r["user_id"], known.NAME, r["display"], r["last_seen"], "task")
        me = config.me_user_id(self._cfg)
        if me:
            for r in reports.dm_spaces(self.conn, self.blocked):
                if r["sender_name"] != me:
                    self.known.remember(r["sender_name"], known.DM_SPACE, r["space_name"], None, "cache")

    def _seed_spaces(self) -> None:
        self.spaces(minimal_messages=0)

    def _by_email(self, email: str) -> str | None:
        # The mirror holds no emails; the Chat API resolves one, given a token.
        from .api import NocacheReader
        try:
            nc = NocacheReader.from_config(self._cfg, self.blocked, self.blocked_assignees, kn=self.known)
        except SystemExit as exc:
            raise SystemExit(f"majordomo: {email}: an email resolves over the Chat API; {exc}") from None
        return nc._by_email(email)

    def resolve_person(self, who: str) -> str:
        return self.known.resolve_person(who, by_email=self._by_email, seed=self._seed_names)

    def resolve_space(self, who: str) -> str:
        return self.known.resolve_space(who, seed=self._seed_spaces)

    def dm_space_for(self, who: str) -> str:
        """The direct-message space with a person, from the file or from the
        mirror (the DM they sent messages in). A blocked DM answers like none."""
        user = self.resolve_person(who)
        found = self.known.dm_space_of(user)
        if found is None:
            rows = [r for r in reports.dm_spaces(self.conn, self.blocked, user=user)]
            if rows:
                found = rows[0]["space_name"]
                self.known.remember(user, known.DM_SPACE, found, None, "cache")
        if found is None or not sieve.allows(self.blocked, found):
            raise SystemExit(f"majordomo: no direct message space with {user}.")
        return found

    # --- the reports -----------------------------------------------------

    def spaces(self, minimal_messages: int = 1) -> list[dict]:
        rows = reports.spaces(self.conn, self.blocked, minimal_messages=minimal_messages)
        for r in rows:
            self.known.remember(r["space_name"], known.NAME, r.get("space_display"), None, "cache")
        self.known.save()
        return rows

    def people(self, *, person: str | None = None, **kw) -> list[dict]:
        rows = reports.people(self.conn, self.blocked, **kw)
        self._seed_names()
        who = self.resolve_person(person) if person else None
        rows = decorate_people(self.known, rows, who)
        self.known.save()
        return sieve.filter_assignees(self.blocked_assignees, rows, id_key="user_id", name_key="display")

    def tasks(self, *, assignee=None, space=None, **filters) -> list[dict]:
        if assignee:
            assignee = self.resolve_person(assignee)
        if space:
            space = self.resolve_space(space)
        rows = reports.tasks(self.conn, self.blocked, assignee=assignee, space=space, **filters)
        for r in rows:
            self.known.remember(r["assignee_user_name"], known.NAME, r.get("assignee"), r.get("created_at"), "task")
        self.known.save()
        return sieve.filter_assignees(self.blocked_assignees, rows)

    def messages(self, space: str | None = None, *, person: str | None = None, **kw) -> list[dict]:
        sender = None
        if space:
            space = self.resolve_space(space)
        if person:
            if space or kw.get("thread"):
                sender = self.resolve_person(person)
            else:
                space = self.dm_space_for(person)
        rows = reports.messages(self.conn, self.blocked, space=space, sender=sender, **kw)
        for r in rows:
            self.known.learn_mentions(r.get("text"), r.pop("annotations_json", None), r.get("create_time"))
        self.known.save()
        return rows


class FreshReader:
    """The `--live` reader: up-to-dateness. Serves the cache (fast, bulk) and tops
    it up from the Chat API with records newer than each space's cache watermark, so
    the answer is current without re-reading what the mirror already holds. People
    and spaces come straight from cache (identities and membership are stable). The
    top-up polls one space when scoped, else only the recently-active spaces
    (reports.active_spaces) — the lever that keeps an unscoped read within quota.
    A third sibling of the CacheReader/NocacheReader duck-type, holding both.
    Names resolve once, through the cache reader, and both halves get ids.
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
            from .api import NocacheReader
            self._nc = NocacheReader.from_config(self._cfg, self.blocked, self.blocked_assignees, kn=self.cache.known)
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

    def tasks(self, *, to_user=None, by_user=None, assignee=None,
              space=None, start=None, end=None, limit=reports.TASK_LIMIT) -> list[dict]:
        if assignee:
            assignee = self.cache.resolve_person(assignee)
        if space:
            space = self.cache.resolve_space(space)
        base = self.cache.tasks(to_user=to_user, by_user=by_user, assignee=assignee,
                                space=space, start=start, end=end, limit=limit)
        targets = self._bounded_targets(self._targets(space))
        if not targets:
            return base
        nc = self._nocache()
        fresh: list[dict] = []
        for sp, wm in targets:
            fresh += nc.tasks(to_user=to_user, by_user=by_user, assignee=assignee, space=sp,
                              start=self._api_start(wm, start), end=end, limit=limit)
        return self._merge(base, fresh, key="source_message_name", time_key="created_at", reverse=True, limit=limit)

    def messages(self, space: str | None = None, *, person=None, thread=None, start=None, end=None,
                 limit=reports.MESSAGE_LIMIT) -> list[dict]:
        if space:
            space = self.cache.resolve_space(space)
        if person:
            if space or thread:
                person = self.cache.resolve_person(person)
            else:
                space, person = self.cache.dm_space_for(person), None
        base = self.cache.messages(space, person=person, thread=thread, start=start, end=end, limit=limit)
        if space:
            targets = [(space, reports.space_watermark(self.cache.conn, space))]
        elif thread:
            from .api import _space_of
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
            fresh += nc.messages(space=sp, person=person, thread=thread,
                                 start=self._api_start(wm, start), end=end, limit=limit)
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
        from .api import NocacheReader
        return NocacheReader.from_config(cfg, blocked, blocked_assignees)
    try:
        cache = CacheReader(db.connect(), blocked, blocked_assignees, cfg=cfg)
    except Exception as exc:
        if source == "cache":
            # --cache promises to fail here rather than fall back; the failure
            # is a one-line answer, not the driver's traceback.
            raise SystemExit(
                f"majordomo: cache unreachable with --cache ({exc}); "
                "drop the flag to fall back to the direct API."
            ) from None
        from .api import NocacheReader
        return NocacheReader.from_config(cfg, blocked, blocked_assignees)
    if source == "live":
        return FreshReader(cache, cfg, blocked, blocked_assignees)
    return cache
