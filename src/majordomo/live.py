"""Live Google Chat reader — the BI-less path. Reads the Chat API directly via
OAuth and decodes tasks itself (decoder.py), so majordomo works without the BI
backend. Needs the `live` extra (google-api-python-client, google-auth). All
records are tagged ``source = "live"``; the sieve is applied here too.

It reuses an existing chat-scoped ``token.json`` (and ``client_secret.json``)
under ``~/.config/majordomo/`` — majordomo does not run an OAuth flow itself.
Names come from the API and the prose @name (no People API). Live is windowed
and slow under Google's read quota.
"""

from __future__ import annotations

import os
from datetime import datetime

from . import config, decoder, sieve

PEOPLE_LIMIT = 1000


def _require_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise SystemExit("majordomo: live mode needs the extra — pip install 'majordomo[live]'.") from exc
    return Credentials, Request, build


def get_credentials(cfg: dict):
    Credentials, Request, _ = _require_google()
    token_file = os.path.expanduser(config.live_token_file(cfg))
    if not os.path.exists(token_file):
        raise SystemExit(
            f"majordomo: no live token at {token_file}. Copy a chat-scoped token.json "
            "(and client_secret.json) there, or use the cache path."
        )
    creds = Credentials.from_authorized_user_file(token_file)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise SystemExit(
                    f"majordomo: live token refresh failed — {exc}. "
                    f"The OAuth client may be revoked; re-authorize and replace {token_file}."
                )
            with open(token_file, "w") as fh:
                fh.write(creds.to_json())
        else:
            raise SystemExit(f"majordomo: live token at {token_file} is invalid and cannot refresh.")
    return creds


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


class LiveReader:
    source = "live"

    def __init__(self, creds=None, blocked: list[str] | None = None, service=None):
        self.blocked = blocked or []
        if service is not None:
            self.chat = service  # injected (tests)
        else:
            _, _, build = _require_google()
            self.chat = build("chat", "v1", credentials=creds, cache_discovery=False)
        self._spaces: list[dict] | None = None

    @classmethod
    def from_config(cls, cfg: dict, blocked: list[str]) -> "LiveReader":
        return cls(get_credentials(cfg), blocked)

    # --- raw API, sieve-aware ---

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
        out, token = [], None
        flt = _time_filter(start, end)
        while True:
            resp = self.chat.spaces().messages().list(
                parent=space_name, filter=flt, pageToken=token, pageSize=1000
            ).execute()
            out.extend(resp.get("messages", []))
            token = resp.get("nextPageToken")
            if not token:
                break
        return out

    # --- reports (same shapes as the cache reader) ---

    def spaces(self) -> list[dict]:
        rows = [{"space_name": s.get("name"), "space_display": s.get("displayName"),
                 "space_type": s.get("spaceType"), "tasks": None} for s in self._all_spaces()]
        return sieve.filter_rows(self.blocked, rows)

    def tasks(self, *, to_user=None, by_user=None, assignee=None, space=None,
              start=None, end=None, limit=1000) -> list[dict]:
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
                aid = t["assignee_user_name"]
                if (to_user and aid != to_user) or (assignee and aid != assignee):
                    continue
                if by_user and sender_of.get(t["source_message_name"]) != by_user:
                    continue
                out.append({
                    "source_message_name": t["source_message_name"],
                    "space_name": t["space_name"],
                    "space_display": disp,
                    "assignee_user_name": aid,
                    "assignee": t["assignee_display"],
                    "title": t["title"],
                    "created_at": _parse_dt(t["created_at"]),
                    "status": "open",
                })
        out.sort(key=lambda r: r["created_at"] or datetime.min, reverse=True)
        return sieve.filter_rows(self.blocked, out)[:limit]

    def people(self) -> list[dict]:
        by_id: dict[str, dict] = {}
        for s in self._all_spaces():
            for m in self._messages(s.get("name"), None, None):
                t = decoder.decode_task(m, s.get("name"))
                if t and t["assignee_user_name"]:
                    e = by_id.setdefault(t["assignee_user_name"],
                                         {"user_id": t["assignee_user_name"], "display": t["assignee_display"], "tasks": 0})
                    e["tasks"] += 1
                    if not e["display"]:
                        e["display"] = t["assignee_display"]
        return sorted(by_id.values(), key=lambda r: r["tasks"], reverse=True)[:PEOPLE_LIMIT]

    def messages(self, space: str, *, start=None, end=None, limit=2000) -> list[dict]:
        if not sieve.allows(self.blocked, space):
            return []
        disp = self._space_display(space)
        rows = [{"name": m.get("name"), "space_name": space, "space_display": disp,
                 "sender_name": (m.get("sender") or {}).get("name"),
                 "sender_type": (m.get("sender") or {}).get("type"),
                 "create_time": _parse_dt(m.get("createTime")), "text": m.get("text")}
                for m in self._messages(space, start, end)]
        return sieve.filter_rows(self.blocked, rows)[:limit]
