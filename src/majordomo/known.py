"""What majordomo has learnt about people and spaces, kept between runs.

A message names its sender by ``users/<id>`` and nothing else; a person's
name exists only as frozen prose, the ``@name`` of a task creation or of an
@-mention, and spellings drift. Every read that surfaces such a fact records
it here, so a later run resolves a name, an email or a display name without a
scan, on the cache path and the direct-API path alike.

One TSV under ``config.state_dir()``, one fact per row: a subject
(``users/<id>`` or ``spaces/<id>``), a kind (``name`` | ``email`` |
``dm_space``), the value, when it was first and last seen (UTC), and the
source that carried it. A fact seen again moves ``last_seen`` only. Nothing is
entered by hand; a renamed person is picked up from the next mention that
carries the new spelling, and the old spellings stay.

Under WORLD_AS_OF the file is read, never written, and a ``name`` first seen
after the bound is ignored: a replayed run must not know a later spelling.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config

FILE_NAME = "known.tsv"
FIELDS = ("subject", "kind", "value", "first_seen", "last_seen", "source")

NAME, EMAIL, DM_SPACE = "name", "email", "dm_space"
_TIME = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stamp(dt: datetime | None) -> str:
    dt = dt or _now()
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime(_TIME)


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, _TIME)


def _fold(name: str) -> str:
    return " ".join(name.split()).casefold()


def is_id(who: str) -> bool:
    return who.startswith("users/")


def is_email(who: str) -> bool:
    return "@" in who and not who.startswith("@")


def mentions_of(text: str | None, annotations) -> list[tuple[str, str]]:
    """The ``(users/<id>, spelling)`` pairs an @-mention carries: the user the
    annotation names and the text at its offset, minus the ``@``. Takes the
    annotation list as the API gives it or the JSON string the mirror keeps."""
    if isinstance(annotations, str):
        try:
            annotations = json.loads(annotations)
        except ValueError:
            return []
    if not isinstance(annotations, list) or not text:
        return []
    out = []
    for a in annotations:
        if not isinstance(a, dict) or a.get("type") != "USER_MENTION":
            continue
        user = ((a.get("userMention") or {}).get("user") or {}).get("name")
        start, length = a.get("startIndex") or 0, a.get("length") or 0
        spelling = text[start:start + length].strip()
        if user and spelling.startswith("@") and len(spelling) > 1:
            out.append((user, " ".join(spelling[1:].split())))
    return out


class Known:
    """The rows of the state file, loaded once and saved after a read learns.

    ``rows`` maps ``(subject, kind, value)`` to ``[first_seen, last_seen,
    source]``. Alternatives weighed: a module-level dict (hidden state, hard to
    isolate in tests) and a dict passed to every reader (each caller then owns
    the save). Holding the path and the rows together is what earns the class.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or (config.state_dir() / FILE_NAME)
        self.rows: dict[tuple[str, str, str], list[str]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) != len(FIELDS):
                continue
            subject, kind, value, first, last, source = parts
            self.rows[(subject, kind, value)] = [first, last, source]

    def save(self) -> None:
        """Write the file atomically (a temp file beside it, then replace),
        once per learning read. A no-op under WORLD_AS_OF or when nothing
        changed."""
        if not self._dirty or config.world_as_of() is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = "\t".join(FIELDS) + "\n" + "".join(
            "\t".join((s, k, v, first, last, source)) + "\n"
            for (s, k, v), (first, last, source) in sorted(self.rows.items())
        )
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".known-", suffix=".tsv")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self._dirty = False

    # --- learning ---------------------------------------------------------

    def remember(self, subject: str, kind: str, value: str | None,
                 seen_at: datetime | None = None, source: str = "") -> None:
        if not subject or not value:
            return
        value = value.strip()
        if not value:
            return
        stamp = _stamp(seen_at)
        key = (subject, kind, value)
        row = self.rows.get(key)
        if row is None:
            self.rows[key] = [stamp, stamp, source]
            self._dirty = True
            return
        if stamp < row[0]:
            row[0] = stamp
            self._dirty = True
        if stamp > row[1]:
            row[1] = stamp
            self._dirty = True

    def learn_mentions(self, text: str | None, annotations, seen_at: datetime | None) -> None:
        for user, spelling in mentions_of(text, annotations):
            self.remember(user, NAME, spelling, seen_at, "mention")

    # --- reading ---------------------------------------------------------

    def _bound(self) -> datetime | None:
        return config.world_as_of()

    def _visible(self, kind: str, first: str) -> bool:
        bound = self._bound()
        return kind != NAME or bound is None or _parse(first) < bound

    def facts(self, subject: str, kind: str) -> list[tuple[str, str, str]]:
        """``(value, first_seen, last_seen)`` for one subject and kind, newest
        last_seen first."""
        out = [(v, first, last) for (s, k, v), (first, last, _src) in self.rows.items()
               if s == subject and k == kind and self._visible(k, first)]
        out.sort(key=lambda t: t[2], reverse=True)
        return out

    def names_of(self, subject: str) -> list[str]:
        return [v for v, _f, _l in self.facts(subject, NAME)]

    def email_of(self, subject: str) -> str | None:
        f = self.facts(subject, EMAIL)
        return f[0][0] if f else None

    def dm_space_of(self, subject: str) -> str | None:
        f = self.facts(subject, DM_SPACE)
        return f[0][0] if f else None

    def space_name_of(self, space: str) -> str | None:
        f = self.facts(space, NAME)
        return f[0][0] if f else None

    def _by_email(self, email: str) -> str | None:
        q = email.strip().casefold()
        for (s, k, v), (first, _l, _src) in self.rows.items():
            if k == EMAIL and v.casefold() == q:
                return s
        return None

    def _match_names(self, prefix: str, who: str) -> dict[str, list[str]]:
        """Subjects with the given prefix whose spellings match ``who``: whole
        spelling first, then substring. Case, spacing and ``@`` are ignored."""
        q = _fold(who.lstrip("@"))
        if not q:
            return {}
        spellings: dict[str, list[str]] = {}
        for (s, k, v), (first, _l, _src) in self.rows.items():
            if k == NAME and s.startswith(prefix) and self._visible(k, first):
                spellings.setdefault(s, []).append(v)
        whole = {s: vs for s, vs in spellings.items() if any(_fold(v) == q for v in vs)}
        if whole:
            return whole
        return {s: vs for s, vs in spellings.items() if any(q in _fold(v) for v in vs)}

    def resolve_person(self, who: str, *, by_email=None, seed=None) -> str:
        """``who`` (a ``users/<id>``, an email, or a name) to a ``users/<id>``.

        ``by_email(email)`` is the backend's lookup for an address the file
        does not hold (the Chat API); ``seed()`` lets the backend pour what it
        knows into the file before a name is matched a second time (the cache
        scan over mentions and tasks). Several matches fail naming each
        candidate; none fails saying so.
        """
        who = who.strip()
        if is_id(who):
            return who
        if is_email(who):
            found = self._by_email(who)
            if found is None and by_email is not None:
                found = by_email(who)
            if found is None:
                raise SystemExit(
                    f"majordomo: no one known by {who}; an email resolves over the "
                    "Chat API (majordomo login), or give users/<id>."
                )
            return found
        matches = self._match_names("users/", who)
        if not matches and seed is not None:
            seed()
            matches = self._match_names("users/", who)
        if len(matches) == 1:
            return next(iter(matches))
        if not matches:
            raise SystemExit(f"majordomo: no one seen as '{who}'; give users/<id> or an email.")
        listed = ", ".join(f"{s} ({', '.join(self.names_of(s))})" for s in sorted(matches))
        raise SystemExit(f"majordomo: '{who}' matches {len(matches)} people: {listed}")

    def resolve_space(self, who: str, *, seed=None) -> str:
        """``who`` (a ``spaces/<id>`` or a display name) to a ``spaces/<id>``.
        ``seed()`` pours the backend's space listing into the file when the
        name is not yet known."""
        who = who.strip()
        if who.startswith("spaces/"):
            return who
        matches = self._match_names("spaces/", who)
        if not matches and seed is not None:
            seed()
            matches = self._match_names("spaces/", who)
        if len(matches) == 1:
            return next(iter(matches))
        if not matches:
            raise SystemExit(f"majordomo: no space named '{who}'; give spaces/<id>.")
        listed = ", ".join(f"{s} ({', '.join(self.names_of(s))})" for s in sorted(matches))
        raise SystemExit(f"majordomo: '{who}' matches {len(matches)} spaces: {listed}")
