"""Who a ``users/<id>`` is and what a ``spaces/<id>`` is called: the one
resolver every report that shows a person goes through, and the name matching
behind every person or space a caller types.

A Chat ``users/<id>`` is the People API's ``people/<id>``. A person is named
from the cache file first and from the People API for ids not there, batched
(``people.getBatchGet``, up to 200 per call), and the answer is written back.
The People API consults only the sources asked for: the profile (which carries
the Workspace domain profile), the signed-in account's saved contacts, and its
"other contacts" are all requested, the last being the only source that names
some people. A profile name wins over a contact label, the label being the
signed-in user's own naming.

Two files under ``config.cache_dir()``: ``people.json`` and ``spaces.json``,
each one JSON object keyed by resource name (``people/<id>``, ``spaces/<id>``)
whose value is ``{"fetched_at": <ISO time>, "person"|"space": <the API object
as returned>}``. Everything there is refetchable, so it is a cache, and it
keeps the API's own form rather than a private schema. A file is rewritten
whole (a temp file, then a rename) when it changed.

Spellings a caller may type that the API does not hold (the frozen ``@name``
of a task or a mention) are derived from the mirror on the cache path and held
for the run only; they are never stored.

Under WORLD_AS_OF the files are read, never written. People API names are
current-state and served like space names (WORLD_AS_OF.design.md §3 rule 1).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

PEOPLE_FILE = "people.json"
SPACES_FILE = "spaces.json"

# people.getBatchGet: https://developers.google.com/people/api/rest/v1/people/getBatchGet
BATCH = 200
PERSON_FIELDS = "names,emailAddresses,metadata"
# ReadSourceType: https://developers.google.com/people/api/rest/v1/ReadSourceType
# Unset, the API reads PROFILE and CONTACT only; OTHER_CONTACT must be asked for.
# READ_SOURCE_TYPE_PROFILE covers ACCOUNT, PROFILE and DOMAIN_PROFILE.
READ_SOURCES = [
    "READ_SOURCE_TYPE_PROFILE",
    "READ_SOURCE_TYPE_CONTACT",
    "READ_SOURCE_TYPE_OTHER_CONTACT",
]
# The scopes those sources need (people.get "Authorization scopes"):
# saved contacts, other contacts, the Workspace directory's domain profiles, and
# the signed-in account's own profile, which is private to it.
PEOPLE_SCOPES = [
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/contacts.other.readonly",
    "https://www.googleapis.com/auth/directory.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# A person fetched longer ago than this is fetched again when next shown, so a
# rename reaches the report; a failed refetch serves the stored one.
MAX_AGE = timedelta(days=30)

# Source types whose name is the person's own, ahead of the account's labels.
_OWN_SOURCES = ("PROFILE", "DOMAIN_PROFILE", "ACCOUNT")

LOGIN_NOTE = (
    "majordomo: this version of majordomo needs more Google permissions than "
    "your saved login grants (the People API, which names people). Run "
    "`majordomo login` to grant them; until then people show as users/<id>."
)
NO_LOGIN_NOTE = (
    "majordomo: people are named through the People API, which needs "
    "`majordomo login`; until then people show as users/<id>."
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str | None) -> datetime | None:
    try:
        return datetime.strptime(stamp or "", "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _fold(name: str) -> str:
    return " ".join(name.split()).casefold()


def is_id(who: str) -> bool:
    return who.startswith("users/")


def is_email(who: str) -> bool:
    return "@" in who and not who.startswith("@")


def person_key(user: str) -> str | None:
    """``users/<id>`` to ``people/<id>``; None for an id People cannot hold
    (a bot's ``users/app``, an email alias)."""
    tail = user[len("users/"):] if user and is_id(user) else ""
    return f"people/{tail}" if tail.isdigit() else None


def _name_text(n: dict) -> str | None:
    text = n.get("displayName") or " ".join(
        x for x in (n.get("givenName"), n.get("familyName")) if x) or n.get("unstructuredName")
    text = " ".join((text or "").split())
    return text or None


def names_in(person: dict | None) -> list[str]:
    """Every distinct name on a People ``Person``, the person's own (profile,
    domain profile, account) ahead of a contact label, primary first within
    each."""
    def rank(n: dict) -> tuple[int, int]:
        meta = n.get("metadata") or {}
        own = (meta.get("source") or {}).get("type") in _OWN_SOURCES
        return (0 if own else 1, 0 if meta.get("primary") else 1)

    out: list[str] = []
    for n in sorted((person or {}).get("names") or [], key=rank):
        text = _name_text(n)
        if text and text not in out:
            out.append(text)
    return out


def emails_in(person: dict | None) -> list[str]:
    return [e["value"] for e in (person or {}).get("emailAddresses") or [] if e.get("value")]


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


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Roster:
    """The two cache files, the People lookup, and this run's spellings.

    ``lookup`` is a callable returning a People API service, or None when the
    token cannot reach it (it raises its own note); it is called at most once,
    and only when an id is missing from the file. Holding the files, the
    lookup and the run's spellings together is what lets one object be the
    single resolver both backends and every report share.
    """

    def __init__(self, directory: Path | None = None, lookup=None):
        self.dir = directory or config.cache_dir()
        self.lookup = lookup
        self._service = None
        self._looked_up = False
        self.people: dict[str, dict] = _load(self.dir / PEOPLE_FILE)
        self.spaces: dict[str, dict] = _load(self.dir / SPACES_FILE)
        self._dirty: set[str] = set()
        # users/<id> -> {spelling: last seen}, and spaces/<id> -> {name: None}:
        # what the mirror carries, for this run's name matching only.
        self._spellings: dict[str, dict[str, datetime | None]] = {}
        self._space_names: dict[str, list[str]] = {}

    # --- the files --------------------------------------------------------

    def save(self) -> None:
        """Rewrite each changed file whole. A no-op under WORLD_AS_OF."""
        if not self._dirty or config.world_as_of() is not None:
            return
        if PEOPLE_FILE in self._dirty:
            _write(self.dir / PEOPLE_FILE, self.people)
        if SPACES_FILE in self._dirty:
            _write(self.dir / SPACES_FILE, self.spaces)
        self._dirty.clear()

    def remember_space(self, space: dict | None) -> None:
        """Store a Chat ``Space`` as the API returned it."""
        name = (space or {}).get("name")
        if not name:
            return
        self.spaces[name] = {"fetched_at": _stamp(_now()), "space": space}
        self._dirty.add(SPACES_FILE)

    # --- people ------------------------------------------------------------

    def _people_service(self):
        if not self._looked_up:
            self._looked_up = True
            self._service = self.lookup() if self.lookup is not None else None
        return self._service

    def _stale(self, key: str) -> bool:
        entry = self.people.get(key)
        if entry is None:
            return True
        fetched = _parse(entry.get("fetched_at"))
        return fetched is None or _now() - fetched > MAX_AGE

    def _fetch(self, keys: list[str]) -> None:
        svc = self._people_service()
        if svc is None:
            return
        for i in range(0, len(keys), BATCH):
            chunk = keys[i:i + BATCH]
            try:
                resp = svc.people().getBatchGet(
                    resourceNames=chunk, personFields=PERSON_FIELDS, sources=READ_SOURCES,
                ).execute(num_retries=3)
            except Exception as exc:
                self._service = None
                status = getattr(getattr(exc, "resp", None), "status", None)
                body = str(getattr(exc, "content", b"") or exc)
                if status in (401, 403) and ("SCOPE" in body.upper() or "insufficient" in body.lower()):
                    config.note_once(LOGIN_NOTE)
                else:
                    config.note_once(f"majordomo: the People API lookup failed ({exc}); "
                                     "people show as users/<id>.")
                return
            stamp = _stamp(_now())
            for r in resp.get("responses") or []:
                person = r.get("person")
                key = r.get("requestedResourceName") or (person or {}).get("resourceName")
                if person and key:
                    self.people[key] = {"fetched_at": stamp, "person": person}
                    self._dirty.add(PEOPLE_FILE)

    def ensure(self, users) -> None:
        """Fetch, in batches, every person among ``users`` the file lacks or
        holds past MAX_AGE."""
        keys = []
        for u in dict.fromkeys(u for u in users if u):
            k = person_key(u)
            if k and self._stale(k):
                keys.append(k)
        if keys:
            self._fetch(keys)

    def names(self, users) -> dict[str, str | None]:
        """The one resolver: each ``users/<id>`` to its display name, from the
        file, else the People API (then written back), else None."""
        users = [u for u in users if u]
        self.ensure(users)
        self.save()
        return {u: self.name_of(u) for u in users}

    def person(self, user: str) -> dict | None:
        k = person_key(user)
        return (self.people.get(k) or {}).get("person") if k else None

    def name_of(self, user: str) -> str | None:
        found = names_in(self.person(user))
        return found[0] if found else None

    def email_of(self, user: str) -> str | None:
        found = emails_in(self.person(user))
        return found[0] if found else None

    def user_by_email(self, email: str) -> str | None:
        q = email.strip().casefold()
        for key, entry in self.people.items():
            if any(e.casefold() == q for e in emails_in(entry.get("person"))):
                return "users/" + key[len("people/"):]
        return None

    # --- this run's spellings ------------------------------------------------

    def learn(self, user: str | None, spelling: str | None, seen: datetime | None = None) -> None:
        spelling = " ".join((spelling or "").split())
        if not user or not spelling:
            return
        known = self._spellings.setdefault(user, {})
        prev = known.get(spelling)
        if spelling not in known or (seen and (prev is None or seen > prev)):
            known[spelling] = seen

    def learn_mentions(self, text: str | None, annotations, seen: datetime | None) -> None:
        for user, spelling in mentions_of(text, annotations):
            self.learn(user, spelling, seen)

    def spellings_of(self, user: str) -> list[str]:
        """The run's prose spellings for a person, newest first."""
        known = self._spellings.get(user) or {}
        return sorted(known, key=lambda s: known[s] or datetime.min, reverse=True)

    def learn_space_name(self, space: str | None, name: str | None) -> None:
        if space and name and name not in self._space_names.setdefault(space, []):
            self._space_names[space].append(name)

    def space_name_of(self, space: str) -> str | None:
        stored = (self.spaces.get(space) or {}).get("space") or {}
        return stored.get("displayName") or next(iter(self._space_names.get(space) or []), None)

    # --- matching what a caller typed ---------------------------------------

    def _people_names(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for key, entry in self.people.items():
            found = names_in(entry.get("person"))
            if found:
                out["users/" + key[len("people/"):]] = list(found)
        for user in self._spellings:
            names = out.setdefault(user, [])
            names += [s for s in self.spellings_of(user) if s not in names]
        return out

    def _space_names_all(self, blocked) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for sid, entry in self.spaces.items():
            name = (entry.get("space") or {}).get("displayName")
            if name:
                out[sid] = [name]
        for sid, names in self._space_names.items():
            out.setdefault(sid, [])
            out[sid] += [n for n in names if n not in out[sid]]
        return {s: v for s, v in out.items() if s not in (blocked or [])}

    @staticmethod
    def _match(candidates: dict[str, list[str]], who: str) -> dict[str, list[str]]:
        """Whole name first, then substring; case, spacing and ``@`` ignored."""
        q = _fold(who.lstrip("@"))
        if not q:
            return {}
        whole = {s: vs for s, vs in candidates.items() if any(_fold(v) == q for v in vs)}
        if whole:
            return whole
        return {s: vs for s, vs in candidates.items() if any(q in _fold(v) for v in vs)}

    def resolve_person(self, who: str, *, by_email=None, seed=None) -> str:
        """``who`` (a ``users/<id>``, an email, or a name) to a ``users/<id>``.

        ``by_email(email)`` is the backend's lookup for an address the file
        does not hold; ``seed()`` pours the backend's spellings in before a
        name is matched a second time (the cache path's mirror scan). Several
        matches fail naming each candidate with its id; none fails saying so.
        """
        who = who.strip()
        if is_id(who):
            return who
        if is_email(who):
            found = self.user_by_email(who)
            if found is None and by_email is not None:
                found = by_email(who)
            if found is None:
                raise SystemExit(
                    f"majordomo: no one known by {who}; an email resolves over the "
                    "Chat API (majordomo login), or give users/<id>."
                )
            return found
        matches = self._match(self._people_names(), who)
        if not matches and seed is not None:
            seed()
            matches = self._match(self._people_names(), who)
        if len(matches) == 1:
            return next(iter(matches))
        if not matches:
            raise SystemExit(f"majordomo: no one seen as '{who}'; give users/<id> or an email.")
        listed = ", ".join(f"{s} ({', '.join(v)})" for s, v in sorted(matches.items()))
        raise SystemExit(f"majordomo: '{who}' matches {len(matches)} people: {listed}")

    def resolve_space(self, who: str, *, seed=None, blocked=None) -> str:
        """``who`` (a ``spaces/<id>`` or a display name) to a ``spaces/<id>``.
        ``seed()`` pours the backend's space listing in when the name is not
        yet known. A blocked space never matches by name."""
        who = who.strip()
        if who.startswith("spaces/"):
            return who
        matches = self._match(self._space_names_all(blocked), who)
        if not matches and seed is not None:
            seed()
            matches = self._match(self._space_names_all(blocked), who)
        if len(matches) == 1:
            return next(iter(matches))
        if not matches:
            raise SystemExit(f"majordomo: no space named '{who}'; give spaces/<id>.")
        listed = ", ".join(f"{s} ({', '.join(v)})" for s, v in sorted(matches.items()))
        raise SystemExit(f"majordomo: '{who}' matches {len(matches)} spaces: {listed}")
