"""Direct Chat API access: OAuth login, the no-cache read path, and send.
Reads the Chat API directly and decodes tasks itself (decoder.py), so majordomo
works without the BI backend. Needs the `api` extra (google-api-python-client,
google-auth). Read records are tagged ``source = "nocache"``; the sieve (spaces
+ assignees) is applied here too. `login` mints the token. Names come from the
API and the prose @name (no People API). A no-cache read is windowed and slow
under Google's read quota, which is why the default path is the BI cache.
"""

from __future__ import annotations

import fnmatch
import os
from datetime import datetime

from . import config, decoder, sieve

PEOPLE_LIMIT = 1000
# The one write scope: creating messages. Nothing else is writable.
SEND_SCOPE = "https://www.googleapis.com/auth/chat.messages.create"
# Scopes for a freshly-minted token: both reads plus send, minted together so
# one login serves every path.
LOGIN_SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    SEND_SCOPE,
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


def login(cfg: dict) -> str:
    """Mint a token via the browser OAuth flow, write it, return its path."""
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
    try:
        flow = InstalledAppFlow.from_client_secrets_file(client_file, LOGIN_SCOPES)
        creds = flow.run_local_server(port=7276, access_type="offline", prompt="consent")
    except Exception:
        # Swallow the raw exception (and its locals): a failed OAuth exchange
        # carries the client secret and authorization code in the traceback.
        raise SystemExit(
            "majordomo: login failed (the OAuth client may be revoked or the "
            f"project disabled). Re-download the Desktop client secret to {client_file} "
            "and check the Cloud project is enabled, then retry."
        )
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    with open(token_file, "w") as fh:
        fh.write(creds.to_json())
    os.chmod(token_file, 0o600)
    return token_file


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


def _dm_space(service, blocked: list[str], to: str) -> str:
    """The existing 1:1 direct-message space with a person, or a clean refusal.

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
    space = found.get("name")
    if not sieve.allows(blocked, space):
        raise SystemExit(absent)
    return space


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
         service=None) -> dict:
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
        creds = get_credentials(cfg)
        if SEND_SCOPE not in (creds.scopes or []):
            raise SystemExit(
                "majordomo: the OAuth token predates send and lacks its scope; "
                "re-run `majordomo login`."
            )
        _, _, build = _require_google()
        service = build("chat", "v1", credentials=creds, cache_discovery=False)
    if to:
        space = _dm_space(service, blocked, to)
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

    def __init__(self, creds=None, blocked=None, blocked_assignees=None, service=None):
        self.blocked = blocked or []
        self.blocked_assignees = blocked_assignees or []
        if service is not None:
            self.chat = service  # injected (tests)
        else:
            _, _, build = _require_google()
            self.chat = build("chat", "v1", credentials=creds, cache_discovery=False)
        self._spaces: list[dict] | None = None

    @classmethod
    def from_config(cls, cfg: dict, blocked: list[str], blocked_assignees: list[str] | None = None) -> "NocacheReader":
        return cls(get_credentials(cfg), blocked, blocked_assignees)

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

    def spaces(self, minimal_messages: int = 1) -> list[dict]:
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
        rows = [{"space_name": s.get("name"), "space_display": s.get("displayName"),
                 "space_type": s.get("spaceType"), "messages": None, "tasks": None}
                for s in found]
        return sieve.filter_rows(self.blocked, rows)

    def tasks(self, *, to_user=None, by_user=None, assignee=None, assignee_name=None,
              space=None, start=None, end=None, limit=1000) -> list[dict]:
        targets = [space] if space else [s.get("name") for s in self._all_spaces()]
        out: list[dict] = []
        for sp in targets:
            if not sieve.allows(self.blocked, sp):
                continue
            msgs = self._messages(sp, start, end)
            decoded = [t for m in msgs if (t := decoder.decode_task(m, sp))]
            decoder.recover_titles(decoded, msgs)
            sender_of = {m.get("name"): (m.get("sender") or {}).get("name") for m in msgs}
            disp = self._space_display(sp)
            for t in decoded:
                aid, adisp = t["assignee_user_name"], t["assignee_display"]
                if (to_user and aid != to_user) or (assignee and aid != assignee):
                    continue
                if assignee_name and not (adisp and fnmatch.fnmatch(adisp, assignee_name)):
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
                    "status": "open",
                })
        out.sort(key=lambda r: r["created_at"] or datetime.min, reverse=True)
        out = sieve.filter_rows(self.blocked, out)
        out = sieve.filter_assignees(self.blocked_assignees, out)
        return out[:limit]

    def people(self, *, start=None, end=None) -> list[dict]:
        by_id: dict[str, dict] = {}
        for s in self._all_spaces():
            for m in self._messages(s.get("name"), start, end):
                sender = (m.get("sender") or {}).get("name")
                if sender:
                    by_id.setdefault(sender, {"user_id": sender, "display": None, "msgs": 0, "tasks": 0})["msgs"] += 1
                t = decoder.decode_task(m, s.get("name"))
                if t and t["assignee_user_name"]:
                    e = by_id.setdefault(t["assignee_user_name"],
                                         {"user_id": t["assignee_user_name"], "display": None, "msgs": 0, "tasks": 0})
                    e["tasks"] += 1
                    if not e["display"]:
                        e["display"] = t["assignee_display"]
        rows = sorted(by_id.values(), key=lambda r: r["msgs"] + r["tasks"], reverse=True)[:PEOPLE_LIMIT]
        return sieve.filter_assignees(self.blocked_assignees, rows, id_key="user_id", name_key="display")

    def messages(self, space: str | None = None, *, thread=None, start=None, end=None, limit=2000) -> list[dict]:
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
        return sieve.filter_rows(self.blocked, rows)[:limit]
