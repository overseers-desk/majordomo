"""Direct Chat API access: OAuth login, the no-cache read path, send, and
attachments. Reads the Chat API directly and decodes tasks itself (decoder.py),
so majordomo works without the BI backend. Needs the `api` extra
(google-api-python-client, google-auth). Read records are tagged
``source = "nocache"``; the sieve (spaces + assignees) is applied here too.
`login` mints the token; ``ensure_command_scopes`` is what a command calls at
its very start, before any Chat or People call, to ask once for everything its
shape (reading the API directly, ``--owner``, ``send``, showing people) needs
that the saved token lacks. ``ensure_scopes`` underneath it is also the lazy
fallback a feature reaches for itself (``people_service``, ``send``,
``owned_spaces``): by the time it runs, the up-front check has already granted
what it could, so it asks nothing new, and if the up-front consent was
declined or failed it does not ask again either, within the same command.
People are named through roster.py, which reads the People API over this
token. A no-cache read is windowed and slow under Google's read quota, which
is why the default path is the BI cache.

`spaces()` also reports whether each space belongs to a Google Workspace
domain (``customer`` set) or a consumer account (unset), free of charge inside
the same `spaces.list` call, plus its `externalUserAllowed` bit. Resolving who
*owns* a space is a separate, costed call per space (`spaces.members.list`
filtered to `ROLE_MANAGER`, Chat's "Owner" role in the UI; see
``MEMBERSHIPS_SCOPE``), opted into with ``owner=True``.
"""

from __future__ import annotations

import errno
import os
import sys
from datetime import datetime

from . import config, decoder, readers, roster, sieve

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
# Needed only to list a space's members, which is how an Owner is found
# (spaces.get's own fields answer "domain or consumer" for free, no scope
# beyond chat.spaces.readonly).
MEMBERSHIPS_SCOPE = "https://www.googleapis.com/auth/chat.memberships.readonly"
# Scopes for a freshly-minted token: the Chat read scopes, send, memberships,
# and the People API reads that name people, minted together so one login
# serves every path.
LOGIN_SCOPES = [
    *CHAT_READ_SCOPES,
    SEND_SCOPE,
    MEMBERSHIPS_SCOPE,
    *roster.PEOPLE_SCOPES,
]
# The loopback port the consent flow listens on for Google's redirect.
CONSENT_PORT = 7276
# How long a consent a command opened by itself waits for the person before
# the command carries on without the new scopes.
CONSENT_TIMEOUT = 180

# What a command's shape needs, keyed by the flag ``ensure_command_scopes``
# takes: reading the Chat API directly, --owner's membership lookup, sending,
# and showing a resolved person (a People-scope need that holds even on a
# cache-only read). One table, so the up-front check and the plain "what does
# this shape need" answer (``scopes_for``) can never drift apart.
_SCOPE_GROUPS: list[tuple[str, list[str], str]] = [
    ("api_read", CHAT_READ_SCOPES, "reading Chat directly"),
    ("owner", [MEMBERSHIPS_SCOPE], "listing space members, for --owner"),
    ("send", [SEND_SCOPE], "sending messages"),
    ("people", roster.PEOPLE_SCOPES, "the People API, which names people"),
]


def _require_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise SystemExit(
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


def _open_quietly(url, new=0, autoraise=True):
    """Open the consent page from a child process whose stdout and stderr are
    discarded, so nothing a browser launcher prints reaches this process's
    stdout: under the MCP server that stream is the protocol."""
    import subprocess

    subprocess.Popen([sys.executable, "-m", "webbrowser", "-t", url],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def _consent(cfg: dict, *, open_browser: bool = True, timeout: int | None = None) -> str:
    """Run Google's consent flow for LOGIN_SCOPES, write the token, return its
    path. Everything the flow prints goes to stderr. With ``timeout`` set, an
    unanswered consent fails after that many seconds rather than waiting."""
    import contextlib
    import webbrowser

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise SystemExit(
            "majordomo: the Google sign-in library is missing, so this "
            "install is incomplete; reinstall majordomo."
        ) from exc
    client_file = os.path.expanduser(config.api_client_file(cfg))
    token_file = os.path.expanduser(config.api_token_file(cfg))
    if not os.path.exists(client_file):
        raise SystemExit(
            f"majordomo: no OAuth client at {client_file}. Create a Desktop OAuth "
            "client in Google Cloud (Chat API enabled) and save it there."
        )
    # include_granted_scopes returns what the account already granted this
    # client alongside the new scopes; oauthlib would otherwise reject a token
    # whose scopes differ from the request.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    import types

    webbrowser.register("majordomo-quiet", None, types.SimpleNamespace(open=_open_quietly))
    try:
        flow = InstalledAppFlow.from_client_secrets_file(client_file, LOGIN_SCOPES)
        with contextlib.redirect_stdout(sys.stderr):
            creds = flow.run_local_server(
                port=CONSENT_PORT, open_browser=open_browser, browser="majordomo-quiet",
                timeout_seconds=timeout,
                authorization_prompt_message="majordomo: grant access in your browser: {url}",
                access_type="offline", prompt="consent", include_granted_scopes="true",
            )
    except Exception as exc:
        # Swallow the raw exception (and its locals, and its message/args):
        # a failed OAuth exchange carries the client secret and authorization
        # code in them. Only the safe reason _consent_reason picks out survives.
        raise SystemExit(
            f"majordomo: login failed or was not completed ({_consent_reason(exc)}). "
            f"Check {client_file} and the Cloud project, then retry `majordomo login`."
        ) from None
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    with open(token_file, "w") as fh:
        fh.write(creds.to_json())
    os.chmod(token_file, 0o600)
    return token_file


def _consent_reason(exc: Exception) -> str:
    """A safe, one-line reason a consent attempt failed: the exception's class
    name, plus a plain sentence for the cases majordomo can tell apart by type
    or errno alone. Never the exception's own message, args, or anything from
    its traceback: a failed OAuth exchange can carry the client secret and
    the authorization code in them, and this must be safe to print (stderr)
    and to carry in the MCP envelope's ``notes`` alike."""
    name = type(exc).__name__
    if name == "WSGITimeoutError":  # google_auth_oauthlib: timeout_seconds elapsed
        return f"{name} (timed out waiting for approval)"
    if isinstance(exc, OSError) and exc.errno == errno.EADDRINUSE:
        return f"{name} (port {CONSENT_PORT} already in use)"
    if name == "AccessDeniedError":  # oauthlib: Google redirected with error=access_denied
        return f"{name} (consent declined / access_denied)"
    return name


def login(cfg: dict) -> str:
    """Mint a token via the browser OAuth flow, write it, return its path."""
    return _consent(cfg)


def consent_mode() -> str | None:
    """How a command may ask for a scope it lacks: "browser" when a browser
    can be opened here, "url" when only a person at a terminal can take the
    printed link, None when nobody is there to answer (cron, CI, a headless
    server)."""
    if os.environ.get("CI"):
        return None
    remote = os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY")
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        display = not remote
    else:
        display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if display:
        return "browser"
    try:
        attended = sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        attended = False
    return "url" if attended else None


def _missing(creds, needed: list[str]) -> list[str]:
    granted = getattr(creds, "scopes", None)
    if granted is None:
        return []  # a token that records no scopes is taken at its word
    return [s for s in needed if s not in granted]


def ensure_scopes(cfg: dict, creds, needed: list[str], purpose: str):
    """``creds`` if it holds ``needed``; else run the consent flow once, when
    someone can answer it, and return the new credentials; else None.

    Never under WORLD_AS_OF (a bounded run is read-only). A declined or
    unanswered consent is not remembered *across commands*: the next command
    that needs the scope asks again. Whoever calls this decides what None
    means: a soft fallback for a display name, a refusal for a send.

    Within one command, though, a scope this already tried and failed to get
    is not tried again: ``cfg`` (loaded once per command, by both front doors)
    carries the set of scopes already asked for, so a later lazy check for the
    same scope, reached after ``ensure_command_scopes`` already tried it and
    was declined, timed out, or found nobody to answer, falls back quietly
    rather than opening the consent page a second time. A scope consent did
    grant needs no such memory: the next check's ``creds`` (re-read from the
    token file) already carries it, so nothing is missing and nothing is
    asked.
    """
    missing = _missing(creds, needed)
    if not missing:
        return creds
    if config.world_as_of() is not None:
        return None
    mode = consent_mode()
    if mode is None:
        return None
    asked = cfg.setdefault("_scopes_asked", set())
    if set(missing) <= asked:
        return None
    asked.update(missing)
    config.note_once(
        "majordomo: this version of majordomo needs more Google permissions than "
        f"your saved login grants ({purpose}); opening Google's consent page to grant them."
        + ("" if mode == "browser" else
           f" Open the link below in a browser on this machine (or forward port {CONSENT_PORT}).")
    )
    try:
        _consent(cfg, open_browser=(mode == "browser"), timeout=CONSENT_TIMEOUT)
        fresh = get_credentials(cfg)
    except SystemExit as exc:
        config.note_once(str(exc))
        return None
    return None if _missing(fresh, needed) else fresh


def scopes_for(*, api_read: bool = False, owner: bool = False, send: bool = False,
               people: bool = False) -> list[str]:
    """The Google scopes a command needs, from what it is about to do:
    ``api_read`` for ``--live``/``--nocache`` and anything (``send``,
    ``attachments``, ``spaces --owner``) that always reads the Chat API
    directly regardless of the source flags; ``owner`` for ``--owner``'s
    membership lookup; ``send`` for sending; ``people`` for any report that
    shows a resolved person's name, a cache-only read included."""
    flags = {"api_read": api_read, "owner": owner, "send": send, "people": people}
    out: list[str] = []
    for key, scopes, _purpose in _SCOPE_GROUPS:
        if flags[key]:
            out += [s for s in scopes if s not in out]
    return out


def ensure_command_scopes(cfg: dict, *, api_read: bool = False, owner: bool = False,
                          send: bool = False, people: bool = False) -> None:
    """Ask, once, for everything this command's shape needs, before any Chat
    or People call. This is the core function both front doors call at the
    very start of a command (INVARIANTS.md: the sieve and the credentials
    live in the core). Without this, a command that both reads slowly and
    eventually needs a new scope (``spaces --owner``, backing off through a
    minute of membership reads before its first name lookup ever ran) could
    spend all of that before finding out consent was needed, or declined.

    A no-op when the shape needs nothing, and when there is no saved token at
    all: consent adds scopes to an existing token (``include_granted_scopes``)
    rather than minting one from nothing, so an install that never ran
    `majordomo login` gets the same "run `majordomo login`" fallback here as
    from the lazy checks, not a surprise consent page. Otherwise this is
    ``ensure_scopes`` under the hood, so every one of its rules apply here
    too: no consent under WORLD_AS_OF, none when nobody can answer, a decline
    not remembered past this command. See its docstring for how a later lazy
    check in the same command avoids asking twice.
    """
    wanted = [(scopes, purpose) for key, scopes, purpose in _SCOPE_GROUPS
              if {"api_read": api_read, "owner": owner, "send": send, "people": people}[key]]
    if not wanted:
        return
    try:
        creds = get_credentials(cfg)
    except SystemExit:
        return
    needed: list[str] = []
    purposes: list[str] = []
    for scopes, purpose in wanted:
        miss = [s for s in scopes if s in _missing(creds, scopes) and s not in needed]
        if miss:
            needed += miss
            purposes.append(purpose)
    if not needed:
        return
    ensure_scopes(cfg, creds, needed, "; ".join(purposes))


def people_service(cfg: dict):
    """A People API service over the saved token, for roster.py; None, with a
    one-line note, when the token cannot name people and could not be made to."""
    try:
        creds = get_credentials(cfg)
    except SystemExit:
        config.note_once(roster.NO_LOGIN_NOTE)
        return None
    creds = ensure_scopes(cfg, creds, roster.PEOPLE_SCOPES, "the People API, which names people")
    if creds is None:
        config.note_once(roster.LOGIN_NOTE)
        return None
    _, _, build = _require_google()
    return build("people", "v1", credentials=creds, cache_discovery=False)


def people_lookup(cfg: dict | None):
    """The roster's lazy People lookup for a config, or None without one."""
    return (lambda: people_service(cfg)) if cfg is not None else None


def get_credentials(cfg: dict):
    import json

    Credentials, Request, _ = _require_google()
    token_file = os.path.expanduser(config.api_token_file(cfg))
    if not os.path.exists(token_file):
        raise SystemExit(
            f"majordomo: no OAuth token at {token_file}. Run `majordomo login` first."
        )

    with open(token_file) as fh:
        tok = json.load(fh)

    # Prefer client_id / client_secret from client_secret.json so that a
    # rotated secret is picked up without re-running login.
    client_file = os.path.expanduser(config.api_client_file(cfg))
    if os.path.exists(client_file):
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
                raise SystemExit(
                    f"majordomo: OAuth token refresh failed — {exc}. "
                    f"Re-run `majordomo login` (the OAuth client may be revoked)."
                )
            with open(token_file, "w") as fh:
                fh.write(creds.to_json())
        else:
            raise SystemExit(f"majordomo: OAuth token at {token_file} is invalid; run `majordomo login`.")
    return creds


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
            raise SystemExit(absent) from None
        raise
    if not sieve.allows(blocked, found.get("name")):
        raise SystemExit(absent)
    return found


def _upload_attachment(service, space: str, path: str) -> dict:
    """Upload one local file to a space and return its attachment ref.

    A missing file fails loud, naming the path. A 404 (the space is gone)
    reuses the not-found wording, so an upload cannot probe a space either.
    """
    import mimetypes

    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise SystemExit(f"majordomo: attachment not found: {path}")
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    media = _media_upload()(path, mimetype=mime)
    try:
        return service.media().upload(
            parent=space, body={"filename": os.path.basename(path)}, media_body=media
        ).execute()
    except Exception as exc:
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            raise SystemExit(f"majordomo: {space}: not found.") from None
        raise


def send(cfg: dict, blocked: list[str], *, space: str | None = None,
         thread: str | None = None, to: str | None = None,
         text: str | None = None, attachments: list[str] | None = None,
         service=None, people=None) -> dict:
    """Create a message in a space, in a thread, or in a person's 1:1 DM;
    returns the created message as the API gives it. Carries text, one or more
    file attachments, or both (at least one is required). The sieve refuses a
    blocked space with the same wording as a space that does not exist, so
    send cannot probe the block list. Refuses under WORLD_AS_OF: a bounded
    run is a replay, and a send would act in the real present.
    """
    if config.world_as_of() is not None:
        raise SystemExit(
            "majordomo: WORLD_AS_OF is set (a replay bound); refusing to send "
            "a real message from a bounded run."
        )
    if (space, thread, to).count(None) != 2:
        raise SystemExit("majordomo: send needs exactly one of space / thread / to.")
    if not text and not attachments:
        raise SystemExit("majordomo: send needs message text, an attachment, or both.")
    body: dict = {}
    kwargs: dict = {}
    if text:
        body["text"] = text
    if thread:
        space, thread_name = _thread_target(thread)
        body["thread"] = {"name": thread_name}
        kwargs["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
    if space and not sieve.allows(blocked, space):
        raise SystemExit(f"majordomo: {space}: not found.")
    if service is None:
        creds = ensure_scopes(cfg, get_credentials(cfg), [SEND_SCOPE], "sending messages")
        if creds is None:
            raise SystemExit(
                "majordomo: this version of majordomo needs more Google permissions "
                "than your saved login grants (sending messages); run `majordomo login` "
                "to grant them."
            )
        _, _, build = _require_google()
        service = build("chat", "v1", credentials=creds, cache_discovery=False)
        people = people_lookup(cfg)
    reader = NocacheReader(blocked=blocked, service=service, me=config.me_user_id(cfg), people=people)
    if to:
        space = reader.dm_space_for(to)
    elif space:
        space = reader.resolve_space(space)
        if not sieve.allows(blocked, space):
            raise SystemExit(f"majordomo: {space}: not found.")
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
            raise SystemExit(f"majordomo: {space}: not found.") from None
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
                 ros: roster.Roster | None = None, me: str | None = None, people=None):
        self.blocked = blocked or []
        self.blocked_assignees = blocked_assignees or []
        if service is not None:
            self.chat = service  # injected (tests)
        else:
            _, _, build = _require_google()
            self.chat = build("chat", "v1", credentials=creds, cache_discovery=False)
        self._spaces: list[dict] | None = None
        # ``people`` is the People lookup for a roster built here; a shared
        # roster brings its own.
        self.roster = ros or roster.Roster(lookup=people)
        # The account's own id, so the other party of a DM can be told apart.
        self.me = me

    @classmethod
    def from_config(cls, cfg: dict, blocked: list[str], blocked_assignees: list[str] | None = None,
                    ros: roster.Roster | None = None, creds=None) -> "NocacheReader":
        return cls(creds or get_credentials(cfg), blocked, blocked_assignees, ros=ros,
                   me=config.me_user_id(cfg), people=people_lookup(cfg))

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
        flt = _time_filter(start, config.world_clamp(end))
        while True:
            resp = self.chat.spaces().messages().list(
                parent=space_name, filter=flt, pageToken=token, pageSize=1000
            ).execute()
            out.extend(resp.get("messages", []))
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
                raise SystemExit(
                    "majordomo: this version of majordomo needs more Google permissions "
                    "than your saved login grants (listing space members, for --owner); "
                    "run `majordomo login` to grant them."
                ) from None
            raise
        members = resp.get("memberships", [])
        return (members[0].get("member") or {}).get("name") if members else None

    def spaces(self, minimal_messages: int = 1, owner: bool = False) -> list[dict]:
        # The Chat API gives no message count cheaply, so minimal_messages is not
        # applied here (the CLI notes the filter is cache-only).
        found = self._all_spaces()
        bound = config.world_as_of()
        if bound is not None:
            # spaces.list takes no date filter, so this is post-filter territory:
            # drop spaces created after the bound. A space without createTime
            # (created before ~mid-2021) is kept as current-state and flagged.
            found = [s for s in found
                     if (ct := _parse_dt(s.get("createTime"))) is None or ct < bound]
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
        return readers.name_assignees(self.roster, self.blocked_assignees, out[:limit])

    def people(self, *, person=None, start=None, end=None) -> list[dict]:
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
            targets = [sp] if sp else [s.get("name") for s in self._all_spaces()]
        elif space:
            targets = [space]
        else:
            return []
        bound = config.world_as_of()
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
                if bound is not None:
                    # Neither store keeps pre-edit bodies: a message edited after
                    # the bound carries its post-edit text. Mark it rather than
                    # drop it: dropping would misreport it as never sent.
                    lu = _parse_dt(m.get("lastUpdateTime"))
                    if lu is not None and lu > bound:
                        row["edited_after_bound"] = True
                rows.append(row)
        # Chat pages a space oldest-first, so the tail is the recent end: the
        # cap keeps that, matching the cache backend row for row.
        return readers.name_senders(self.roster, sieve.filter_rows(self.blocked, rows)[-limit:])


def owned_spaces(cfg: dict, blocked: list[str], *, minimal_messages: int = 1, owner: bool = False) -> list[dict]:
    """Spaces with their domain/consumer status and, with ``owner=True``, who
    owns each one. Always reads the direct API regardless of the caller's
    --cache/--live/--nocache choice, the same as `attachments`: both front
    doors call this one function rather than building a `NocacheReader`
    themselves, so the "always direct" exception has one home.
    """
    creds = get_credentials(cfg)
    if owner:
        # A token short of the memberships scope gets the consent flow once;
        # declined or unanswerable, the first member read refuses.
        creds = ensure_scopes(cfg, creds, [MEMBERSHIPS_SCOPE], "listing space members, for --owner") or creds
    return NocacheReader.from_config(cfg, blocked, creds=creds).spaces(
        minimal_messages=minimal_messages, owner=owner)


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


def _safe_basename(content_name: str | None, attachment_name: str | None) -> str:
    """The filename to write, stripped to a bare basename.

    Whoever posted the file chose its name, so it is untrusted input: a
    contentName of "../../x" would otherwise write outside the destination.
    A name that is empty or all path once stripped fails loud rather than
    inventing one, because a silently renamed download is a file nobody can
    match back to the message.
    """
    base = os.path.basename((content_name or "").strip())
    if not base or base in (".", ".."):
        raise SystemExit(
            f"majordomo: attachment {attachment_name} has no usable filename "
            f"({content_name!r}); download it by another means."
        )
    return base


def _fetch(service, row: dict, dest_dir: str) -> str:
    """Download one attachment's bytes into dest_dir; return the path written.

    Refuses to overwrite: a file already at the target path is left as it is
    and named, because clobbering a download the caller may have edited is
    worse than stopping. This also catches two attachments on one message that
    share a filename, the second hitting the first one's write.
    """
    import io

    if row["source"] != UPLOADED:
        raise SystemExit(
            f"majordomo: {row['content_name']} is a {row['source']} attachment, "
            "held in Drive rather than Chat; majordomo reads Chat only."
        )
    path = os.path.join(dest_dir, _safe_basename(row["content_name"], row["attachment_name"]))
    if os.path.exists(path):
        raise SystemExit(f"majordomo: {path} already exists; not overwriting.")
    request = service.media().download_media(resourceName=row["resource_name"])
    buf = io.BytesIO()
    downloader = _media_download()(buf, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    return path


def attachments(cfg: dict, blocked: list[str], *, space: str | None = None,
                thread: str | None = None, message: str | None = None,
                person: str | None = None,
                start: datetime | None = None, end: datetime | None = None,
                limit: int = 500, download_to: str | None = None,
                service=None, people=None) -> list[dict]:
    """The files posted in a space, a thread, on one message, or by a person.

    One of space / thread / message / person names the scope; a person alone
    means the direct-message space with them, and a person with a space keeps
    only the files that person posted there. Reports one row
    per file; with ``download_to`` set, also writes each file into that
    directory and adds the path written under ``path``. The API is the only
    path, the mirror holding no attachment rows, so this needs `majordomo
    login`. WORLD_AS_OF bounds it like any read: a file posted after the bound
    is not reported.
    """
    given = sorted(n for n, v in (("space", space), ("thread", thread), ("message", message), ("person", person)) if v)
    if not (len(given) == 1 or given == ["person", "space"]):
        raise SystemExit("majordomo: attachments needs one of space / thread / message / person "
                         "(a person may also be paired with a space).")
    if download_to is not None:
        download_to = os.path.expanduser(download_to)
        if not os.path.isdir(download_to):
            raise SystemExit(f"majordomo: no such directory: {download_to}")

    if service is None:
        _, _, build = _require_google()
        service = build("chat", "v1", credentials=get_credentials(cfg), cache_discovery=False)
        people = people_lookup(cfg)

    bound = config.world_as_of()
    reader = NocacheReader(blocked=blocked, service=service, me=config.me_user_id(cfg), people=people)
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
        raise SystemExit(f"majordomo: {scope_space}: not found.")
    rows: list[dict] = []
    if message:
        # One message named: a single get, rather than paging its whole space.
        try:
            msg = service.spaces().messages().get(name=message).execute()
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) == 404:
                raise SystemExit(f"majordomo: {message}: not found.") from None
            raise
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
            row["path"] = _fetch(service, row, download_to)
    return rows
