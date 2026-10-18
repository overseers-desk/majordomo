"""Direct Chat API access: the saved OAuth token, the no-cache read path,
send, and attachments. Reads the Chat API directly and decodes tasks itself
(decoder.py), so majordomo works without the BI backend. Needs the `api` extra
(google-api-python-client, google-auth). Read records are tagged
``source = "nocache"``; the sieve (spaces + assignees) is applied here too.
The token is minted by Google's consent flow, which a front door runs
(frontdoor.py). ``ensure_command_scopes`` names, from a command's shape, every
scope the saved token lacks, by raising ``ScopeError``; ``ensure_scopes`` is
the same check made by a feature that reaches for a scope itself, which then
falls back or refuses. People are named through roster.py, which reads the
People API over this token. A command run as an ``[identity.NAME]``
(``--as NAME``) holds a Chat app's service-account key instead of this token
(``chat_credentials`` chooses), and sends and reads as that app, which has no
consent flow, no People API and no Google Tasks list.

A no-cache read is windowed and slow under Google's read quota, which
is why the default path is the BI cache.

`spaces()` also reports whether each space belongs to a Google Workspace
domain (``customer`` set) or a consumer account (unset), free of charge inside
the same `spaces.list` call, plus its `externalUserAllowed` bit. Resolving who
*owns* a space is a separate, costed call per space (`spaces.members.list`
filtered to `ROLE_MANAGER`, Chat's "Owner" role in the UI; see
``MEMBERSHIPS_SCOPE``), opted into with ``owner=True``.
"""

from __future__ import annotations

import functools
import os
from datetime import datetime

from . import config, decoder, readers, roster, sieve
from .errors import MajordomoError, NotFound, ScopeError

PEOPLE_LIMIT = 1000
# Reading spaces and messages over the Chat API directly: --live, --nocache,
# and the paths (send, attachments, spaces --owner) that always read the API
# regardless of the source flags.
CHAT_READ_SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
]
# The one write scope: creating messages. Nothing else is writable.
SEND_SCOPE = "https://www.googleapis.com/auth/chat.messages.create"
# A Chat app's own scope, held by a service-account key: it sends as the app,
# and lists the spaces and finds the DMs the app is a member of.
BOT_SCOPE = "https://www.googleapis.com/auth/chat.bot"
# A Chat app reading the public messages of the spaces it is a member of; a
# Google Workspace administrator approves it for the app once.
APP_MESSAGES_SCOPE = "https://www.googleapis.com/auth/chat.app.messages.readonly"
APP_READ_SCOPES = [BOT_SCOPE, APP_MESSAGES_SCOPE]
# Needed only to list a space's members, which is how an Owner is found
# (spaces.get's own fields answer "domain or consumer" for free, no scope
# beyond chat.spaces.readonly).
MEMBERSHIPS_SCOPE = "https://www.googleapis.com/auth/chat.memberships.readonly"
# The signed-in person's own Google Tasks lists, which hold their copy of every
# task assigned to them in a Chat space (DESIGN.md, "Task reconstruction");
# read for `tasks --to-me`'s fill of notes, due dates and titles.
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"
OWN_PURPOSE = "your own Google Tasks list, for the tasks assigned to you"
# Scopes for a freshly-minted token: the Chat read scopes, send, memberships,
# the Tasks list, and the People API reads that name people, minted together
# so one login serves every path.
LOGIN_SCOPES = [
    *CHAT_READ_SCOPES,
    SEND_SCOPE,
    MEMBERSHIPS_SCOPE,
    TASKS_SCOPE,
    *roster.PEOPLE_SCOPES,
]

# What a command's shape needs, keyed by the flag ``ensure_command_scopes``
# takes: reading the Chat API directly, --owner's membership lookup, sending,
# showing a resolved person (a People-scope need that holds even on a
# cache-only read), and --to-me's fill from the person's own task list. One
# table, so the up-front check and the plain "what does this shape need"
# answer (``scopes_for``) can never drift apart.
_SCOPE_GROUPS: list[tuple[str, list[str], str]] = [
    ("api_read", CHAT_READ_SCOPES, "reading Chat directly"),
    ("owner", [MEMBERSHIPS_SCOPE], "listing space members, for --owner"),
    ("send", [SEND_SCOPE], "sending messages"),
    ("people", roster.PEOPLE_SCOPES, "the People API, which names people"),
    ("own", [TASKS_SCOPE], OWN_PURPOSE),
]


def _require_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise MajordomoError(
            "majordomo: the Google client libraries are missing, so this "
            "install is incomplete; reinstall majordomo."
        ) from exc
    return Credentials, Request, build


def _media_upload():
    from googleapiclient.http import MediaFileUpload

    return MediaFileUpload


def _media_download():
    from googleapiclient.http import MediaIoBaseDownload

    return MediaIoBaseDownload


def _missing(creds, needed: list[str]) -> list[str]:
    granted = getattr(creds, "scopes", None)
    if granted is None:
        return []  # a token that records no scopes is taken at its word
    return [s for s in needed if s not in granted]


def _short_of(scopes: list[str], purpose: str) -> ScopeError:
    return ScopeError(
        "majordomo: this version of majordomo needs more Google permissions than "
        f"your saved login grants ({purpose}); run `majordomo login` to grant them.",
        scopes=scopes, purpose=purpose,
    )


def ensure_scopes(creds, needed: list[str], purpose: str):
    """``creds`` if it holds ``needed``; else ScopeError carrying the missing
    scopes and ``purpose``. Whoever calls this decides what the refusal
    means: a soft fallback for a display name, a refusal for a send."""
    missing = _missing(creds, needed)
    if missing:
        raise _short_of(missing, purpose)
    return creds


def scopes_for(*, api_read: bool = False, owner: bool = False, send: bool = False,
               people: bool = False, own: bool = False) -> list[str]:
    """The Google scopes a command needs, from what it is about to do:
    ``api_read`` for ``--live``/``--nocache`` and anything (``send``,
    ``attachments``, ``spaces --owner``) that always reads the Chat API
    directly regardless of the source flags; ``owner`` for ``--owner``'s
    membership lookup; ``send`` for sending; ``people`` for any report that
    shows a resolved person's name, a cache-only read included; ``own`` for
    ``tasks --to-me``'s fill from the person's own task list."""
    flags = {"api_read": api_read, "owner": owner, "send": send, "people": people, "own": own}
    out: list[str] = []
    for key, scopes, _purpose in _SCOPE_GROUPS:
        if flags[key]:
            out += [s for s in scopes if s not in out]
    return out


def ensure_command_scopes(cfg: dict, *, api_read: bool = False, owner: bool = False,
                          send: bool = False, people: bool = False, own: bool = False) -> None:
    """Every scope this command's shape needs that the saved token lacks,
    raised as one ScopeError (``.scopes`` their union, ``.purpose`` what each
    group is for) before any Chat or People call. Both front doors call this
    at the very start of a command, through frontdoor.up_front, which may ask
    Google for them: a command that both reads slowly and eventually needs a
    new scope (``spaces --owner``, backing off through a minute of membership
    reads before its first name lookup ran) learns of the gap before it has
    spent any of that.

    Quiet when the shape needs nothing, as a Chat app (its key carries its
    scopes and there is no consent to ask; what the app cannot serve refuses
    for itself), under a WORLD_AS_OF bound (a bounded run asks no consent),
    and when there is no saved token at all: an install that never ran
    `majordomo login` meets the "no OAuth token at …" error
    ``get_credentials`` raises, which sends it to `majordomo login`, rather
    than a consent page it did not ask for.
    """
    wanted = [(scopes, purpose) for key, scopes, purpose in _SCOPE_GROUPS
              if {"api_read": api_read, "owner": owner, "send": send, "people": people, "own": own}[key]]
    if not wanted or config.identity(cfg) or config.world_as_of(cfg) is not None:
        return
    try:
        creds = get_credentials(cfg)
    except MajordomoError:
        return
    needed: list[str] = []
    purposes: list[str] = []
    for scopes, purpose in wanted:
        miss = [s for s in scopes if s in _missing(creds, scopes) and s not in needed]
        if miss:
            needed += miss
            purposes.append(purpose)
    if needed:
        ensure_scopes(creds, needed, "; ".join(purposes))


def people_service(cfg: dict, credentials=None):
    """A People API service over the command's credentials, for roster.py;
    None, with a one-line note, when there is no usable token or it cannot
    name people."""
    try:
        creds = ensure_scopes(chat_credentials(cfg, credentials), roster.PEOPLE_SCOPES,
                              "the People API, which names people")
    except ScopeError:
        config.note_once(roster.LOGIN_NOTE)
        return None
    except MajordomoError:
        config.note_once(roster.NO_LOGIN_NOTE)
        return None
    _, _, build = _require_google()
    return build("people", "v1", credentials=creds, cache_discovery=False)


def people_lookup(cfg: dict | None, credentials=None):
    """The roster's lazy People lookup for a config. None without one, and
    None as a Chat app, which has no People API: its people are named from
    the name cache, else from the names the Chat API gives it, with no note, there being no login to fix."""
    if cfg is None or config.identity(cfg):
        return None
    return lambda: people_service(cfg, credentials)


OWN_NOTE = (
    "majordomo: the Google login majordomo reads with cannot read your Google Tasks "
    "list, so the tasks assigned to you show without notes or due dates; on your "
    "saved login, `majordomo login` grants it."
)


def _pages(call, key: str, **kw):
    """Every item of a paginated list call."""
    token = None
    while True:
        resp = call(pageToken=token, **kw).execute()
        yield from resp.get(key, [])
        token = resp.get("nextPageToken")
        if not token:
            return


def own_tasks(cfg: dict, service=None, credentials=None) -> dict | None:
    """The signed-in person's tasks assigned from Chat spaces, from every one
    of their Google Tasks lists, indexed for readers.fill_own: ``by_thread``
    maps (space, thread) to the task made from that thread's message,
    ``by_space`` maps a space to (updated time, task) pairs for the tasks
    created fresh there. Read over ``credentials`` when handed in, else the
    saved login. None, with a one-line note, when there is no usable token or
    it cannot read the lists; None too under WORLD_AS_OF, the lists being
    current-state. ``service`` is injected by tests."""
    if config.world_as_of(cfg) is not None:
        return None
    if service is None:
        try:
            creds = ensure_scopes(credentials if credentials is not None else get_credentials(cfg),
                                  [TASKS_SCOPE], OWN_PURPOSE)
        except MajordomoError:
            config.note_once(OWN_NOTE)
            return None
        _, _, build = _require_google()
        service = build("tasks", "v1", credentials=creds, cache_discovery=False)
    index: dict = {"by_thread": {}, "by_space": {}}
    for tl in _pages(service.tasklists().list, "items", maxResults=100):
        for t in _pages(service.tasks().list, "items", tasklist=tl["id"], maxResults=100,
                        showAssigned=True, showCompleted=True, showHidden=True):
            info = t.get("assignmentInfo") or {}
            if info.get("surfaceType") != "SPACE":
                continue
            space = (info.get("spaceInfo") or {}).get("space")
            path = (info.get("linkToTask") or "").split("/space/")[-1].split("/")
            if len(path) >= 3:
                index["by_thread"][(space, path[1])] = t
            else:
                index["by_space"].setdefault(space, []).append((datetime.fromisoformat(t["updated"]).timestamp(), t))
    return index


def own_tasks_lookup(cfg: dict | None, credentials=None):
    """A memoised own_tasks for a config, so one command lists the API once
    whatever readers it builds; None without a config, and None as a Chat
    app, which holds no Google Tasks list."""
    if cfg is None or config.identity(cfg):
        return None
    return functools.cache(lambda: own_tasks(cfg, credentials=credentials))


def get_credentials(cfg: dict):
    import json

    Credentials, Request, _ = _require_google()
    token_file = config.api_token_file(cfg)
    if not token_file:
        raise MajordomoError(
            "majordomo: no OAuth token configured; set [api].token_file, or pass "
            "credentials= to make_reader, tasks or messages."
        )
    if not os.path.exists(token_file):
        raise MajordomoError(
            f"majordomo: no OAuth token at {token_file}. Run `majordomo login` first."
        )

    with open(token_file) as fh:
        tok = json.load(fh)

    # Prefer client_id / client_secret from client_secret.json so that a
    # rotated secret is picked up without re-running login.
    client_file = config.api_client_file(cfg)
    if client_file and os.path.exists(client_file):
        with open(client_file) as fh:
            raw = json.load(fh)
        block = raw.get("installed") or raw.get("web") or {}
        client_id = block.get("client_id") or tok.get("client_id")
        client_secret = block.get("client_secret") or tok.get("client_secret")
    else:
        client_id = tok.get("client_id")
        client_secret = tok.get("client_secret")

    creds = Credentials(
        token=tok.get("token"),
        refresh_token=tok.get("refresh_token"),
        token_uri=tok.get("token_uri"),
        client_id=client_id,
        client_secret=client_secret,
        scopes=tok.get("scopes"),
    )
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise MajordomoError(
                    f"majordomo: OAuth token refresh failed — {exc}. "
                    f"Re-run `majordomo login` (the OAuth client may be revoked)."
                )
            with open(token_file, "w") as fh:
                fh.write(creds.to_json())
        else:
            raise MajordomoError(f"majordomo: OAuth token at {token_file} is invalid; run `majordomo login`.")
    return creds


def _service_account_credentials(ident: dict, scopes: list[str]):
    """The Chat app's credentials from its service-account key file. The key
    is never rewritten; the access token it signs for lives in memory."""
    _require_google()
    from google.oauth2 import service_account

    path = ident["service_account_file"]
    if not os.path.exists(path):
        raise MajordomoError(f"majordomo: no service-account key at {path} for [identity.{ident['name']}].")
    return service_account.Credentials.from_service_account_file(path, scopes=scopes)


def chat_credentials(cfg: dict, credentials=None):
    """The credentials a Chat read runs with: ``credentials`` when handed in,
    else, under an ``[identity.NAME]``, the Chat app's key with its read
    scopes, else the saved login."""
    if credentials is not None:
        return credentials
    ident = config.identity(cfg)
    if ident:
        return _service_account_credentials(ident, APP_READ_SCOPES)
    return get_credentials(cfg)


def _thread_target(thread: str) -> tuple[str, str]:
    """Resolve a reply target to (space, thread resource name).

    Accepts what ``messages --thread`` accepts, a thread or any message name in
    it: the part before the first "." names the thread, and a
    spaces/X/messages/T key maps to the thread spaces/X/threads/T.
    """
    key = thread.split(".")[0]
    return "/".join(key.split("/")[:2]), key.replace("/messages/", "/threads/")


def _dm_space(service, blocked: list[str], to: str) -> dict:
    """The existing 1:1 direct-message ``Space`` with a person, as the API
    returns it, or a clean refusal.

    ``to`` is users/<id>, a bare id, or an email (the API takes the email as
    an alias for the id). A DM the sieve blocks answers exactly like one that
    does not exist, so the resolved space id stays unspoken.
    """
    user = to if to.startswith("users/") else f"users/{to}"
    absent = f"majordomo: no direct message space with {user}."
    try:
        found = service.spaces().findDirectMessage(name=user).execute()
    except Exception as exc:
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            raise NotFound(absent) from None
        raise
    if not sieve.allows(blocked, found.get("name")):
        raise NotFound(absent)
    return found


def _upload_attachment(service, space: str, path: str) -> dict:
    """Upload one local file to a space and return its attachment ref.

    A missing file fails loud, naming the path. A 404 (the space is gone)
    reuses the not-found wording, so an upload cannot probe a space either.
    """
    import mimetypes

    if not os.path.isfile(path):
        raise MajordomoError(f"majordomo: attachment not found: {path}")
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    media = _media_upload()(path, mimetype=mime)
    try:
        return service.media().upload(
            parent=space, body={"filename": os.path.basename(path)}, media_body=media
        ).execute()
    except Exception as exc:
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            raise NotFound(f"majordomo: {space}: not found.") from None
        raise


def send(cfg: dict, *, space: str | None = None,
         thread: str | None = None, to: str | None = None,
         text: str | None = None, attachments: list[str] | None = None,
         service=None, people=None, cache_dir=None) -> dict:
    """Create a message in a space, in a thread, or in a person's 1:1 DM;
    returns the created message as the API gives it. Carries text, one or more
    file attachments, or both (at least one is required). The sieve, the
    cfg's ``[sieve].block_spaces``, refuses a blocked space with the same
    wording as a space that does not exist, so send cannot probe the block
    list. Refuses under WORLD_AS_OF: a bounded
    run is a replay, and a send would act in the real present. Run as an
    ``[identity.NAME]``, it sends as that Chat app: into spaces the app is a
    member of, ``to`` reaching the app's own DM, and with no attachments. The
    key is asked for ``chat.bot`` alone, which needs no administrator's
    approval. ``cache_dir`` holds the name cache a ``to`` or ``space`` given
    by name resolves through (roster.py); without one, names resolve from
    what this call fetches.
    """
    if config.world_as_of(cfg) is not None:
        raise MajordomoError(
            "majordomo: WORLD_AS_OF is set (a replay bound); refusing to send "
            "a real message from a bounded run."
        )
    if (space, thread, to).count(None) != 2:
        raise MajordomoError("majordomo: send needs exactly one of space / thread / to.")
    if not text and not attachments:
        raise MajordomoError("majordomo: send needs message text, an attachment, or both.")
    blocked = config.block_spaces(cfg)
    ident = config.identity(cfg)
    if ident and attachments:
        raise MajordomoError(
            f"majordomo: {ident['name']} is a Chat app; Google accepts file uploads "
            "only from a person's login."
        )
    body: dict = {}
    kwargs: dict = {}
    if text:
        body["text"] = text
    if thread:
        space, thread_name = _thread_target(thread)
        body["thread"] = {"name": thread_name}
        kwargs["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
    if space and not sieve.allows(blocked, space):
        raise NotFound(f"majordomo: {space}: not found.")
    if service is None:
        if ident:
            creds = _service_account_credentials(ident, [BOT_SCOPE])
        else:
            creds = ensure_scopes(get_credentials(cfg), [SEND_SCOPE], "sending messages")
            people = people_lookup(cfg)
        _, _, build = _require_google()
        service = build("chat", "v1", credentials=creds, cache_discovery=False)
    reader = NocacheReader(blocked=blocked, service=service, me=config.me_user_id(cfg), people=people,
                           cache_dir=cache_dir)
    if to:
        space = reader.dm_space_for(to)
    elif space:
        space = reader.resolve_space(space)
    reader.roster.save()
    # The space is now resolved and sieve-cleared; upload only after that, so a
    # blocked or absent target is refused before any file leaves the machine.
    if attachments:
        body["attachment"] = [_upload_attachment(service, space, p) for p in attachments]
    try:
        return service.spaces().messages().create(parent=space, body=body, **kwargs).execute()
    except Exception as exc:
        # A 404 answers exactly like the sieve above, one wording for both.
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            raise NotFound(f"majordomo: {space}: not found.") from None
        raise


def _rfc3339(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _time_filter(start: datetime | None, end: datetime | None) -> str:
    parts = []
    if start:
        parts.append(f'createTime > "{_rfc3339(start)}"')
    if end:
        parts.append(f'createTime < "{_rfc3339(end)}"')
    return " AND ".join(parts)


def _parse_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).replace(tzinfo=None)
    except ValueError:
        return None


def _space_of(thread_key: str) -> str | None:
    return "/".join(thread_key.split("/")[:2]) if thread_key.startswith("spaces/") else None


class NocacheReader:
    source = "nocache"

    def __init__(self, creds=None, blocked=None, blocked_assignees=None, service=None,
                 ros: roster.Roster | None = None, me: str | None = None, people=None, own=None,
                 bound: datetime | None = None, identity: dict | None = None, cache_dir=None):
        self.blocked = blocked or []
        self.bound = bound  # WORLD_AS_OF, naive UTC; None reads unbounded
        self.identity = identity  # config.identity: the Chat app read as; None for a person's login
        self.blocked_assignees = blocked_assignees or []
        self.own = own  # the person's own task list, for --to-me's fill (readers.fill_own)
        if service is not None:
            self.chat = service  # injected (tests)
        else:
            _, _, build = _require_google()
            self.chat = build("chat", "v1", credentials=creds, cache_discovery=False)
        self._spaces: list[dict] | None = None
        # ``people`` and ``cache_dir`` make the roster built here; a shared
        # roster brings its own.
        self.roster = ros or roster.Roster(cache_dir, lookup=people, write_back=bound is None)
        # The account's own id, so the other party of a DM can be told apart.
        self.me = me

    @classmethod
    def from_config(cls, cfg: dict, blocked: list[str], blocked_assignees: list[str] | None = None,
                    ros: roster.Roster | None = None, creds=None, own=None, cache_dir=None) -> "NocacheReader":
        return cls(chat_credentials(cfg, creds), blocked, blocked_assignees, ros=ros,
                   me=config.me_user_id(cfg), people=people_lookup(cfg, creds),
                   own=own or own_tasks_lookup(cfg, creds), bound=config.world_as_of(cfg),
                   identity=config.identity(cfg), cache_dir=cache_dir)

    # --- people and spaces by name ---------------------------------------

    def _counterpart(self, space: str) -> str | None:
        """The human in a DM who is not the account itself, from its messages."""
        resp = self.chat.spaces().messages().list(parent=space, pageSize=100).execute()
        for m in resp.get("messages", []):
            sender = m.get("sender") or {}
            if sender.get("type") == "HUMAN" and sender.get("name") and sender.get("name") != self.me:
                return sender["name"]
        return None

    def _find_dm(self, who: str) -> str:
        found = _dm_space(self.chat, self.blocked, who)
        self.roster.remember_space(found)
        self.roster.save()
        return found["name"]

    def _by_email(self, email: str) -> str | None:
        """An address the People cache does not hold: the other human in the
        DM findDirectMessage returns for it, then named through the roster,
        which also stores the email where People carries it."""
        user = self._counterpart(self._find_dm(email))
        if user:
            self.roster.ensure([user])
            self.roster.save()
        return user

    def resolve_person(self, who: str) -> str:
        return self.roster.resolve_person(who, by_email=self._by_email)

    def resolve_space(self, who: str) -> str:
        return self.roster.resolve_space(who, seed=self._all_spaces, blocked=self.blocked)

    def dm_space_for(self, who: str) -> str:
        """The direct-message space with a person: an email or id straight
        through the API's lookup, a name resolved to its id first."""
        who = who.strip()
        if not (roster.is_id(who) or roster.is_email(who)):
            who = self.resolve_person(who)
        return self._find_dm(who)

    def _all_spaces(self) -> list[dict]:
        if self._spaces is None:
            out, token = [], None
            while True:
                resp = self.chat.spaces().list(pageToken=token, pageSize=1000).execute()
                out.extend(resp.get("spaces", []))
                token = resp.get("nextPageToken")
                if not token:
                    break
            self._spaces = [s for s in out if sieve.allows(self.blocked, s.get("name"))]
            for s in self._spaces:
                self.roster.remember_space(s)
            self.roster.save()
        return self._spaces

    def _space_display(self, name: str) -> str | None:
        for s in self._all_spaces():
            if s.get("name") == name:
                return s.get("displayName")
        return None

    def _messages(self, space_name: str, start, end) -> list[dict]:
        # WORLD_AS_OF is enforced here, at the one _time_filter caller, so
        # tasks / people / messages are all server-bounded by createTime.
        out, token = [], None
        flt = _time_filter(start, config.world_clamp(end, self.bound))
        while True:
            try:
                resp = self.chat.spaces().messages().list(
                    parent=space_name, filter=flt, pageToken=token, pageSize=1000
                ).execute()
            except Exception as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                # An absent space answers in the sieve's words for a blocked one.
                if status == 404:
                    raise NotFound(f"majordomo: {space_name}: not found.") from None
                if status == 403 and self.identity:
                    raise ScopeError(
                        f"majordomo: Google refused the Chat app {self.identity['name']} a read of {space_name}: "
                        f"the app is not a member of that space, or it lacks {APP_MESSAGES_SCOPE}, "
                        "which a Google Workspace administrator approves for the app once.",
                        scopes=[APP_MESSAGES_SCOPE], purpose="reading messages as a Chat app",
                    ) from None
                raise
            page = resp.get("messages", [])
            for m in page:
                _observe(self.roster, m)
            out.extend(page)
            token = resp.get("nextPageToken")
            if not token:
                break
        return out

    def _owner_of(self, space: str) -> str | None:
        """The ``users/<id>`` with ``Membership.role = ROLE_MANAGER``, Chat's
        "Owner" role in the UI (its separate `ROLE_ASSISTANT_MANAGER` is the
        UI's "Manager" and is not this). None when the space carries no owner
        membership (a direct message, for instance). Under plain user auth the
        API gives back only the member's ``name``, never a display name, so
        the caller names it through the roster like any other id.
        """
        try:
            # One read per space: a burst over every space trips the project's
            # membership_reads quota with 429, which num_retries backs off and
            # retries (the client's own exponential backoff, up to ~64 s).
            resp = self.chat.spaces().members().list(
                parent=space, filter='role = "ROLE_MANAGER"', pageSize=1
            ).execute(num_retries=6)
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) == 403:
                raise _short_of([MEMBERSHIPS_SCOPE], "listing space members, for --owner") from None
            raise
        members = resp.get("memberships", [])
        return (members[0].get("member") or {}).get("name") if members else None

    def spaces(self, minimal_messages: int = 1, owner: bool = False) -> list[dict]:
        # The Chat API gives no message count cheaply, so minimal_messages is not
        # applied here (the CLI notes the filter is cache-only).
        found = self._all_spaces()
        if self.bound is not None:
            # spaces.list takes no date filter, so this is post-filter territory:
            # drop spaces created after the bound. A space without createTime
            # (created before ~mid-2021) is kept as current-state and flagged.
            found = [s for s in found
                     if (ct := _parse_dt(s.get("createTime"))) is None or ct < self.bound]
        rows = []
        for s in found:
            # `customer` (a Workspace domain id) comes back free on the same
            # call; its absence is the consumer/personal-account case. A
            # DIRECT_MESSAGE space never carries it either way, so domain_owned
            # is only meaningful for named spaces and group chats.
            customer = s.get("customer")
            rows.append({
                "space_name": s.get("name"), "space_display": s.get("displayName"),
                "space_type": s.get("spaceType"), "messages": None, "tasks": None,
                "customer": customer, "domain_owned": customer is not None,
                "external_user_allowed": bool(s.get("externalUserAllowed")),
            })
        rows = sieve.filter_rows(self.blocked, rows)
        if owner:
            for r in rows:
                # A direct message has no Owner role, so it costs no lookup.
                r["owner_user_id"] = (None if r["space_type"] == "DIRECT_MESSAGE"
                                      else self._owner_of(r["space_name"]))
            named = self.roster.names(r["owner_user_id"] for r in rows)
            for r in rows:
                r["owner_display"] = named.get(r["owner_user_id"])
        return rows

    def tasks(self, *, to_user=None, by_user=None, assignee=None,
              space=None, start=None, end=None, limit=1000) -> list[dict]:
        if assignee:
            assignee = self.resolve_person(assignee)
        if space:
            space = self.resolve_space(space)
        targets = [space] if space else [s.get("name") for s in self._all_spaces()]
        out: list[dict] = []
        for sp in targets:
            if not sieve.allows(self.blocked, sp):
                continue
            msgs = self._messages(sp, start, end)
            decoded = [t for m in msgs if (t := decoder.decode_task(m, sp))]
            decoder.recover_titles(decoded, msgs)
            decoder.apply_lifecycle(decoded, msgs)
            sender_of = {m.get("name"): (m.get("sender") or {}).get("name") for m in msgs}
            disp = self._space_display(sp)
            for t in decoded:
                aid, adisp = t["assignee_user_name"], t["assignee_display"]
                if (to_user and aid != to_user) or (assignee and aid != assignee):
                    continue
                if by_user and sender_of.get(t["source_message_name"]) != by_user:
                    continue
                out.append({
                    "source_message_name": t["source_message_name"],
                    "space_name": t["space_name"],
                    "space_display": disp,
                    "assignee_user_name": aid,
                    "assignee": adisp,
                    "title": t["title"],
                    "created_at": _parse_dt(t["created_at"]),
                    "status": t["status"],
                })
        out.sort(key=lambda r: r["created_at"] or datetime.min, reverse=True)
        out = sieve.filter_rows(self.blocked, out)
        out = sieve.filter_assignees(self.blocked_assignees, out)
        rows = readers.name_assignees(self.roster, self.blocked_assignees, out[:limit])
        return readers.fill_own(rows, self.own if to_user and to_user == self.me else None)

    def people(self, *, person=None, start=None, end=None) -> list[dict]:
        readers.person_only(self.identity, "the people report")
        by_id: dict[str, dict] = {}
        dm_of: dict[str, str] = {}
        for s in self._all_spaces():
            is_dm = s.get("spaceType") == "DIRECT_MESSAGE"
            for m in self._messages(s.get("name"), start, end):
                self.roster.learn_mentions(m.get("text"), m.get("annotations"), _parse_dt(m.get("createTime")))
                sender = (m.get("sender") or {}).get("name")
                if sender:
                    by_id.setdefault(sender, {"user_id": sender, "display": None, "msgs": 0, "tasks": 0})["msgs"] += 1
                    if is_dm and sender != self.me:
                        dm_of.setdefault(sender, s.get("name"))
                t = decoder.decode_task(m, s.get("name"))
                if t and t["assignee_user_name"]:
                    e = by_id.setdefault(t["assignee_user_name"],
                                         {"user_id": t["assignee_user_name"], "display": None, "msgs": 0, "tasks": 0})
                    e["tasks"] += 1
                    if not e["display"]:
                        e["display"] = t["assignee_display"]
                    self.roster.learn(t["assignee_user_name"], t["assignee_display"], _parse_dt(t["created_at"]))
        rows = sorted(by_id.values(), key=lambda r: r["msgs"] + r["tasks"], reverse=True)[:PEOPLE_LIMIT]
        who = self.resolve_person(person) if person else None
        rows = sieve.filter_assignees(self.blocked_assignees, rows, id_key="user_id", name_key="display")
        rows = readers.decorate_people(self.roster, rows, who, dm_of)
        return sieve.filter_assignees(self.blocked_assignees, rows, id_key="user_id", name_key="display")

    def messages(self, space: str | None = None, *, person=None, thread=None, start=None, end=None,
                 limit=2000) -> list[dict]:
        sender = None
        if space:
            space = self.resolve_space(space)
        if person:
            if space or thread:
                sender = self.resolve_person(person)
            else:
                space = self.dm_space_for(person)
        if thread:
            key = thread.split(".")[0]
            sp = _space_of(key)
            targets = [self.resolve_space(sp)] if sp else [s.get("name") for s in self._all_spaces()]
        elif space:
            targets = [space]
        else:
            return []
        rows: list[dict] = []
        for sp in targets:
            if sp and not sieve.allows(self.blocked, sp):
                continue
            for m in self._messages(sp, start, end):
                if thread and m.get("name", "").split(".")[0] != key:
                    continue
                if sender and (m.get("sender") or {}).get("name") != sender:
                    continue
                row = {"name": m.get("name"), "space_name": sp, "space_display": self._space_display(sp),
                       "sender_name": (m.get("sender") or {}).get("name"),
                       "sender_type": (m.get("sender") or {}).get("type"),
                       "create_time": _parse_dt(m.get("createTime")), "text": m.get("text")}
                if self.bound is not None:
                    # Neither store keeps pre-edit bodies: a message edited after
                    # the bound carries its post-edit text. Mark it rather than
                    # drop it: dropping would misreport it as never sent.
                    lu = _parse_dt(m.get("lastUpdateTime"))
                    if lu is not None and lu > self.bound:
                        row["edited_after_bound"] = True
                rows.append(row)
        # Chat pages a space oldest-first, so the tail is the recent end: the
        # cap keeps that, matching the cache backend row for row.
        return readers.name_senders(self.roster, sieve.filter_rows(self.blocked, rows)[-limit:])


def owned_spaces(cfg: dict, blocked: list[str], *, minimal_messages: int = 1, owner: bool = False,
                 cache_dir=None) -> list[dict]:
    """Spaces with their domain/consumer status and, with ``owner=True``, who
    owns each one. Always reads the direct API regardless of the caller's
    --cache/--live/--nocache choice, the same as `attachments`: both front
    doors call this one function rather than building a `NocacheReader`
    themselves, so the "always direct" exception has one home. A token short
    of the memberships scope is refused by the first member read, as a
    ScopeError. Owners are named through the name cache in ``cache_dir``.
    """
    if owner:
        readers.person_only(config.identity(cfg), "finding space owners")
    reader = NocacheReader.from_config(cfg, blocked, cache_dir=cache_dir)
    return reader.spaces(minimal_messages=minimal_messages, owner=owner)


# --- attachments ---------------------------------------------------------
#
# The files posted to a space, listed and fetched. This is a read, but it sits
# outside the reader seam beside `send` rather than inside it: the seam exists
# so cache and API answer interchangeably, and the mirror carries no file rows
# at all, so a CacheReader method here could only ever refuse. Both front doors
# call this one function, and the sieve is applied in it.

# The one attachment kind Chat serves as bytes. A Drive-backed attachment
# carries a driveDataRef instead and is fetched through the Drive API, which
# majordomo holds no scope for.
UPLOADED = "UPLOADED_CONTENT"


def _observe(ros: roster.Roster, msg: dict) -> None:
    """Hand the roster this message's users as the Chat API gave them: the
    sender and each mention, whose ``displayName`` it fills for an app caller."""
    ros.observe(msg.get("sender"))
    for a in msg.get("annotations") or []:
        ros.observe(((a.get("userMention") or {}).get("user")) if isinstance(a, dict) else None)


def _attachment_rows(msg: dict, space: str, space_display: str | None) -> list[dict]:
    """One row per file hanging off a message, in the order Chat lists them."""
    rows = []
    for att in msg.get("attachment") or []:
        rows.append({
            "attachment_name": att.get("name"),
            "message_name": msg.get("name"),
            "space_name": space,
            "space_display": space_display,
            "sender_name": (msg.get("sender") or {}).get("name"),
            "create_time": _parse_dt(msg.get("createTime")),
            "content_name": att.get("contentName"),
            "content_type": att.get("contentType"),
            "source": att.get("source"),
            "resource_name": (att.get("attachmentDataRef") or {}).get("resourceName"),
        })
    return rows


def _safe_basename(content_name: str | None, attachment_name: str | None,
                   content_type: str | None = None) -> str:
    """The filename to write, stripped to a bare basename.

    Whoever posted the file chose its name, so it is untrusted input: a
    contentName of "../../x" would otherwise write outside the destination.
    A name that is empty or all path once stripped is refused rather than
    invented, since a name with nothing of the posted one in it could not be
    matched back to the message. A name with no extension (Chat posts pasted
    images as "untitled") takes one from the content type, so the file opens.
    """
    import mimetypes

    base = os.path.basename((content_name or "").strip())
    if not base or base in (".", ".."):
        raise MajordomoError(
            f"majordomo: attachment {attachment_name} has no usable filename "
            f"({content_name!r}); download it by another means."
        )
    if not os.path.splitext(base)[1] and content_type:
        base += mimetypes.guess_extension(content_type) or ""
    return base


def _fetch(service, row: dict, dest_dir: str) -> str:
    """Download one attachment's bytes into dest_dir; return the path written.

    Never overwrites: a file already at the target path, whether the caller's
    or an earlier attachment in the same run with the same posted name, is
    left as it is, and this one is written beside it as name-2, name-3 and so
    on. The row's ``path`` names which file is which.
    """
    import io

    if row["source"] != UPLOADED:
        raise MajordomoError(
            f"majordomo: {row['content_name']} is a {row['source']} attachment, "
            "held in Drive rather than Chat; majordomo reads Chat only."
        )
    base = _safe_basename(row["content_name"], row["attachment_name"], row["content_type"])
    request = service.media().download_media(resourceName=row["resource_name"])
    buf = io.BytesIO()
    downloader = _media_download()(buf, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    stem, ext = os.path.splitext(base)
    n = 1
    while True:
        path = os.path.join(dest_dir, base if n == 1 else f"{stem}-{n}{ext}")
        try:
            # "x" claims the name atomically, so no check-then-write race.
            with open(path, "xb") as fh:
                fh.write(buf.getvalue())
            return path
        except FileExistsError:
            n += 1


def attachments(cfg: dict, blocked: list[str], *, space: str | None = None,
                thread: str | None = None, message: str | None = None,
                person: str | None = None,
                start: datetime | None = None, end: datetime | None = None,
                limit: int = 500, download_to: str | None = None,
                service=None, people=None, cache_dir=None) -> list[dict]:
    """The files posted in a space, a thread, on one message, or by a person.

    One of space / thread / message / person names the scope; a person alone
    means the direct-message space with them, and a person with a space keeps
    only the files that person posted there. Reports one row
    per file; with ``download_to`` set, also writes each file into that
    directory and adds the path written under ``path``, or, for a file that
    could not be saved, the reason under ``error``; the other files are still
    fetched. The API is the only
    path, the mirror holding no attachment rows, so this needs `majordomo
    login`. WORLD_AS_OF bounds it like any read: a file posted after the bound
    is not reported. Senders are named through the name cache in
    ``cache_dir``.
    """
    readers.person_only(config.identity(cfg), "reading attachments")
    given = sorted(n for n, v in (("space", space), ("thread", thread), ("message", message), ("person", person)) if v)
    if not (len(given) == 1 or given == ["person", "space"]):
        raise MajordomoError("majordomo: attachments needs one of space / thread / message / person "
                             "(a person may also be paired with a space).")
    if download_to is not None and not os.path.isdir(download_to):
        raise MajordomoError(f"majordomo: no such directory: {download_to}")

    if service is None:
        _, _, build = _require_google()
        service = build("chat", "v1", credentials=chat_credentials(cfg), cache_discovery=False)
        people = people_lookup(cfg)

    bound = config.world_as_of(cfg)
    reader = NocacheReader(blocked=blocked, service=service, me=config.me_user_id(cfg), people=people,
                           bound=bound, cache_dir=cache_dir)
    sender = None
    if space:
        space = reader.resolve_space(space)
    if person:
        if space:
            sender = reader.resolve_person(person)
        else:
            space = reader.dm_space_for(person)
    # Chat suffixes a threaded message's id with ".<n>", so cutting at the dot
    # leaves the thread key, and its first two segments are the space.
    scope_space = space or _space_of((thread or message).split(".")[0])
    if scope_space and not sieve.allows(blocked, scope_space):
        # Worded as the sieve words every block: indistinguishable from absent.
        raise NotFound(f"majordomo: {scope_space}: not found.")
    rows: list[dict] = []
    if message:
        # One message named: a single get, rather than paging its whole space.
        try:
            msg = service.spaces().messages().get(name=message).execute()
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) == 404:
                raise NotFound(f"majordomo: {message}: not found.") from None
            raise
        _observe(reader.roster, msg)
        created = _parse_dt(msg.get("createTime"))
        if not (bound is not None and created is not None and created >= bound):
            rows = _attachment_rows(msg, scope_space, reader._space_display(scope_space))
    else:
        # messages.list carries each message's attachment field already, so the
        # files come out of the same paged read the message report does, with
        # no per-message fetch. _messages applies the WORLD_AS_OF clamp.
        key = thread.split(".")[0] if thread else None
        display = reader._space_display(scope_space)
        for msg in reader._messages(scope_space, start, end):
            if key and msg.get("name", "").split(".")[0] != key:
                continue
            if sender and (msg.get("sender") or {}).get("name") != sender:
                continue
            rows += _attachment_rows(msg, scope_space, display)

    # The newest files are the ones a capped listing is asked for, and the API
    # pages oldest-first, so the cap takes the tail.
    rows = readers.name_senders(reader.roster, sieve.filter_rows(blocked, rows)[-limit:])
    if download_to is not None:
        for row in rows:
            try:
                row["path"] = _fetch(service, row, download_to)
            except MajordomoError as exc:
                row["error"] = str(exc)
            except Exception as exc:  # a failed fetch or write, e.g. HTTP 5xx, disk full
                row["error"] = f"majordomo: {row['content_name']}: {exc}"
    return rows
