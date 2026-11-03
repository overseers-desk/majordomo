"""Answer Google Chat direct messages as an account.

Each account named in imperson.toml is read through majordomo under that
account's own login. Every new message in its direct messages is handed to the
Claude Agent SDK session kept for that conversation, which answers through the
reply tool, or stays silent, by the account's rule. What the session can do
beyond talking comes from its skills.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import majordomo
from majordomo import dates

from . import ledger, tenant
from .books import Books

HERE = Path(__file__).resolve().parent
CONFIG = Path("~/.config/majordomo/imperson.toml")
STATE = Path("~/.local/state/majordomo-imperson")
CACHE = Path("~/.cache/majordomo-imperson")
SERVER = "imperson"
ALLOWED = ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]
DENIED = ["Bash", "Write", "Edit", "NotebookEdit", "Task"]
SEND_MODES = ("off", "test", "live")


@dataclass
class Turn:
    """One incoming message, as the skills' tools see it."""
    tenant: Path
    state: Path
    now: datetime
    sender: str
    message: str
    space: str
    uploads: list[dict]          # this message's files in the books: id, path (None when not fetched), name, ...
    post: Callable[[str, str, list[str]], None]
    books: Books
    account: str


@dataclass
class Tenant:
    """A business, shared by the accounts that work for it: its repository, the cache of the files imperson reads
    from it, and its books' settings."""
    name: str
    repo: str
    token: str
    files: Path
    branch: str | None = None
    db: dict = field(default_factory=dict)
    fetch: Callable[..., list[str]] = tenant.refresh
    fresh: bool = False

    def refresh(self) -> None:
        """Bring the cache up to the repository, once a run; a failed fetch leaves the cache as it stands, and stops
        the account only when there is no cache to work from."""
        if self.fresh:
            return
        try:
            changed = self.fetch(self.repo, self.token, self.files, branch=self.branch)
        except Exception as exc:
            if not (self.files / tenant.STATE).exists():
                raise RuntimeError(f"the tenant {self.name}'s files could not be fetched and none are cached: {exc}") from exc
            print(f"imperson: tenant {self.name}: working from the cached files, as the refresh failed: {exc}", file=sys.stderr)
        else:
            if changed:
                print(f"imperson: tenant {self.name}: {len(changed)} file(s) refreshed from {self.repo}", file=sys.stderr)
        self.fresh = True


def _text(text: str, error: bool = False) -> dict:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if error:
        out["is_error"] = True
    return out


def reply_tool(say: Callable[[str, list[str]], None]):
    from claude_agent_sdk import tool

    @tool("reply", "Send a message to the person who wrote, in this conversation. It is the only way they hear "
          "from you: call it for each message you send, and not at all to stay silent. `files` attaches files "
          "by path, such as ones saved from their message.",
          {"type": "object", "properties": {"text": {"type": "string"},
                                            "files": {"type": "array", "items": {"type": "string"}}},
           "required": ["text"]})
    async def reply(args: dict) -> dict:
        say(args["text"], list(args.get("files") or []))
        return _text("Sent.")
    return reply


def _confine(dirs: list[Path]):
    from claude_agent_sdk import HookMatcher

    roots = [d.resolve() for d in dirs]

    async def confine(data: dict, tool_use_id: str | None, context: Any) -> dict:
        args = data.get("tool_input") or {}
        target = args.get("file_path") or args.get("path")
        if target is None:
            return {}
        reached = Path(target).expanduser().resolve()
        if any(reached == r or r in reached.parents for r in roots):
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": f"{reached} is outside what this conversation may read."}}
    return HookMatcher(matcher="Read|Glob|Grep", hooks=[confine])


def converse(prompt: str, *, session: str | None, cwd: Path, system_prompt: str, skills: list[str],
             tools: list, read_dirs: list[Path], env: dict[str, str]) -> str | None:
    """One turn of a conversation through the Agent SDK; the session's id, to resume it at the next message.
    ``env`` gives Claude Code its own configuration folder, so the plugins, hooks and instructions of whoever
    runs imperson stay out of the conversation."""
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, create_sdk_mcp_server, query

    async def turn() -> str | None:
        options = ClaudeAgentOptions(
            resume=session, cwd=cwd, system_prompt=system_prompt, skills=skills,
            setting_sources=["project"], strict_mcp_config=True,
            mcp_servers={SERVER: create_sdk_mcp_server(SERVER, tools=tools)},
            allowed_tools=[*ALLOWED, *(f"mcp__{SERVER}__{t.name}" for t in tools)],
            disallowed_tools=DENIED, permission_mode="dontAsk",
            hooks={"PreToolUse": [_confine(read_dirs)]}, max_turns=40, env=env,
            max_buffer_size=8 * 1024 * 1024)  # the SDK reports a Read of a photo back whole; Claude Code caps such an image near 1500 x 2000
        found = session
        async for m in query(prompt=prompt, options=options):
            if isinstance(m, ResultMessage):
                found = m.session_id or found
                if m.subtype != "success":
                    raise RuntimeError(f"the turn ended: {m.subtype}")
        return found
    return asyncio.run(turn())


def _line(u: dict) -> str:
    """How the conversation is told of one upload of its message."""
    if u["path"] is None:
        return f"A file in this message could not be fetched: {u['name']} ({u['error']}) (upload {u['id']})"
    same = u.get("identical_to")
    also = (f"; not kept in the books: {u['error']}" if not u["kept"] else
            f"; the same file as upload {same['id']}, received {same['received_at'].isoformat()} in message "
            f"{same['message']}" if same else "")
    return f"File saved from this message: {u['path']} (upload {u['id']}, {u['media_type']}{also})"


def whoami(cfg: dict) -> str:
    """The Chat id of the person whose token this is: People's ``people/<id>`` is Chat's ``users/<id>``."""
    from majordomo import api

    people = api.people_service(cfg)
    if people is None:
        raise SystemExit("the token cannot reach the People API, so the account cannot tell its own messages")
    me = people.people().get(resourceName="people/me", personFields="metadata").execute()["resourceName"]
    return me.replace("people/", "users/", 1)


class Account:
    def __init__(self, name: str, table: dict, *, tenants: dict[str, Tenant], books=Books, claude_token: str = "",
                 chat=majordomo, talk=converse, me=whoami, now: datetime | None = None, state_root: Path = STATE):
        self.name, self.table, self.chat, self.talk = name, table, chat, talk
        self.send = table.get("send", "off")
        if self.send not in SEND_MODES:
            raise SystemExit(f"[account.{name}] send must be one of {', '.join(SEND_MODES)}")
        self.test_to = table.get("test_correspondent")
        if self.send == "test" and not self.test_to:
            raise SystemExit(f"[account.{name}] send = \"test\" needs test_correspondent")
        if table.get("tenant") not in tenants:
            raise SystemExit(f"[account.{name}] tenant = {table.get('tenant')!r}, but there is no [tenant.{table.get('tenant')}]")
        self.tenant = tenants[table["tenant"]]
        self.books = books(self.tenant.db, "imperson_" if self.send == "live" else "imperson_test_")
        keys = Path(table["keys"]).expanduser()
        self.cfg = chat.load_config({"api": {"token_file": str(keys / "token.json"),
                                             "client_file": str(keys / "client_secret.json")}})
        self.me = me(self.cfg)
        self.skills = list(table.get("skills", []))
        self.state = state_root.expanduser() / name
        self.env = {"CLAUDE_CONFIG_DIR": str(self.state / "claude"), "CLAUDE_CODE_OAUTH_TOKEN": claude_token}
        self.now = now or datetime.now(timezone.utc)
        self.out: list[str] = []
        saved = self.state / "state.json"
        self.saved = json.loads(saved.read_text()) if saved.exists() else {"mark": None, "handled": {}, "sessions": {}}
        self.saved.setdefault("read", {})

    def save(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "state.json").write_text(json.dumps(self.saved, indent=1))

    def gate(self, *, to: str | None = None, space: str | None = None, text: str, files=()) -> None:
        """Send as the account under its send mode: off prints, test sends only to the test correspondent."""
        self.out.append(f"[{self.name} -> {to or space}] {text}" + (f" (+{len(files)} file)" if files else ""))
        if self.send == "off":
            return
        if self.send == "test":
            to, space = self.test_to, None
        self.chat.send(self.cfg, to=to, space=space, text=text, attachments=list(files) or None,
                       cache_dir=str(self.state / "cache"))

    def system_prompt(self) -> str:
        rule = self.table.get("speak_when")
        speaking = (f"Decide whether to speak by this rule, written by the account's owner:\n{rule.strip()}\n"
                    "When the rule does not call for you to speak, read the message and do not call reply."
                    if rule else "Answer every message.")
        return (f"You answer Google Chat direct messages as {self.table.get('address', self.name)}. Each turn is "
                "one message someone sent to this account: who sent it, when, its text, and any files saved from "
                "it for you to read. People see only what you send with the reply tool; nothing else you write "
                f"reaches them.\n{speaking}\nUse your skills where they fit. The business's rules and your skills can change "
                "between messages, so for each message a skill applies to, invoke the skill and read its files again "
                "rather than relying on an earlier turn. A question about the weather or about "
                f"what is on nearby, with no place named, is about {self.table.get('location', 'the business')}. "
                f"The business's files are in {self.tenant.files}. Today is {self.now.date().isoformat()}.")

    def workspace(self) -> Path:
        """The session's working folder: it links imperson's skills and the business's own, in the business's
        `.imperson/skills/`, which takes a name's place when both have it, and holds nothing else."""
        ws = self.state / "workspace"
        folder = ws / ".claude" / "skills"
        if folder.is_symlink():
            folder.unlink()
        folder.mkdir(parents=True, exist_ok=True)
        skills = {p.name: p for root in (HERE / "skills", self.tenant.files / ".imperson" / "skills") if root.is_dir()
                  for p in sorted(root.iterdir()) if p.is_dir()}
        for link in folder.iterdir():
            if link.is_symlink() and skills.get(link.name) != link.readlink():
                link.unlink()
        for name, source in skills.items():
            if not (folder / name).is_symlink():
                (folder / name).symlink_to(source)
        return ws

    def rules(self) -> dict[str, str]:
        """A digest of each file the skills work from, imperson's own and every one of the business's in the cache,
        to tell a conversation what changed."""
        roots = [HERE / "skills" / s for s in self.skills] + [self.tenant.files]
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for root in roots if root.is_dir()
                for p in sorted(root.rglob("*")) if p.is_file() and p != self.tenant.files / tenant.STATE}

    def upload(self, row: dict, a: dict) -> dict:
        """One file of a message, written to the books; what the conversation is told of it."""
        path = a.get("path")
        u = self.books.add_upload(attachment=a["attachment_name"], account=self.name, message=row["name"],
                                  sender=a.get("sender_name") or row["sender_name"], name=a.get("content_name") or "",
                                  media_type=a.get("content_type"),
                                  received_at=dates.utc(row.get("create_time")) or self.now,
                                  content=Path(path).read_bytes() if path else None, error=None if path else a.get("error"))
        return {"id": u["id"], "path": path, "name": u["name"], "media_type": u["media_type"],
                "kept": u["file_id"] is not None, "error": a.get("error") or u["error"], "identical_to": u["identical_to"]}

    def handle(self, space: str, row: dict, uploads: list[dict]) -> None:
        sender = row["sender_name"]
        turn = Turn(tenant=self.tenant.files, state=self.state, now=self.now, sender=sender, message=row["name"],
                    space=space, uploads=uploads, post=lambda sp, text, fs: self.gate(space=sp, text=text, files=fs),
                    books=self.books, account=self.name)
        tools = [reply_tool(lambda text, fs: self.gate(to=sender, text=text, files=fs))]
        for skill in self.skills:
            try:
                if (self.tenant.files / ".imperson" / skill / "policy.yaml").exists():
                    tools += ledger.tools(turn, skill)
            except Exception as exc:  # a skill whose files are missing leaves the conversation itself running
                print(f"imperson: {self.name}: the {skill} skill's tools are unavailable: {exc}", file=sys.stderr)
        when = dates.utc(row.get("create_time"))
        prompt = (f"From {row.get('sender_display') or sender} ({sender}), {when.isoformat() if when else ''}:\n"
                  f"{row.get('text') or '(no text)'}" + "".join(f"\n{_line(u)}" for u in uploads))
        sessions, rules = self.saved["sessions"], self.rules()
        seen = self.saved["read"].get(space, {})
        changed = [f for f, digest in rules.items() if seen.get(f) != digest]
        if sessions.get(space) and changed:
            prompt = ("These files changed since this conversation last read them, so what it read before is out of "
                      "date; read them again before relying on them:\n" + "\n".join(changed) + "\n\n" + prompt)
        sessions[space] = self.talk(prompt, session=sessions.get(space), cwd=self.workspace(),
                                    system_prompt=self.system_prompt(), skills=self.skills, tools=tools,
                                    read_dirs=[self.state / "work", self.tenant.files], env=self.env)
        self.saved["read"][space] = rules
        self.saved["handled"].setdefault(space, []).append(row["name"])
        self.saved["handled"][space] = self.saved["handled"][space][-500:]
        self.save()

    def run(self) -> list[str]:
        since = dates.utc(self.saved["mark"]) or self.now - timedelta(days=1)
        cache = str(self.state / "cache")
        failed = False
        for sp in self.chat.make_reader(self.cfg, "nocache", cache_dir=cache).spaces():
            last = dates.utc(sp.get("last_active"))
            if sp.get("space_type") != "DIRECT_MESSAGE" or last is None or last <= since:
                continue
            space = sp["space_name"]
            done = set(self.saved["handled"].get(space, []))
            rows = [r for r in self.chat.messages(self.cfg, space, start=since, source="nocache", cache_dir=cache)
                    if r.get("sender_name") != self.me and r["name"] not in done
                    and (self.send != "test" or r.get("sender_name") == self.test_to)]
            if not rows:
                continue
            self.tenant.refresh()
            self.books.ensure_schema()
            with_files = {a["message_name"] for a in self.chat.attachments(self.cfg, space=space, start=since,
                                                                             cache_dir=cache)}
            for row in sorted(rows, key=lambda r: dates.utc(r.get("create_time")) or self.now):
                uploads: list[dict] = []
                try:
                    if row["name"] in with_files:
                        dest = self.state / "work" / row["name"].replace("/", "_")
                        dest.mkdir(parents=True, exist_ok=True)
                        saved = self.chat.attachments(self.cfg, message=row["name"], download_to=str(dest),
                                                      cache_dir=cache)
                        uploads = [self.upload(row, a) for a in saved]
                    self.handle(space, row, uploads)
                except Exception as exc:  # the mark stays, so the next run reads this message and the ones after it again
                    print(f"imperson: {self.name}: {row['name']}: {exc}", file=sys.stderr)
                    failed = True
                    break
        if not failed:
            self.saved["mark"] = self.now.isoformat()
            self.save()
        return self.out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="imperson", description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(CONFIG), help="the accounts to answer as (default: %(default)s)")
    parser.add_argument("--account", help="answer as this account alone")
    args = parser.parse_args(argv)
    with open(Path(args.config).expanduser(), "rb") as fh:
        settings = tomllib.load(fh)
    accounts = settings.get("account", {})
    token = settings.get("claude_token")
    if not token:
        raise SystemExit(f"imperson: {args.config} has no claude_token; make one with `claude setup-token`")
    tenants = {}
    for name, t in settings.get("tenant", {}).items():
        if missing := [k for k in ("repo", "github_token") if not t.get(k)]:
            raise SystemExit(f"imperson: [tenant.{name}] needs {' and '.join(missing)}")
        tenants[name] = Tenant(name, t["repo"], t["github_token"], CACHE.expanduser() / "tenant" / name,
                               branch=t.get("branch"), db=dict(t.get("db", {})))
    for name, table in accounts.items():
        if args.account and name != args.account:
            continue
        try:
            for line in Account(name, table, tenants=tenants, claude_token=token).run():
                print(line)
        except Exception as exc:  # one account's failure leaves the others to run
            print(f"imperson: {name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
