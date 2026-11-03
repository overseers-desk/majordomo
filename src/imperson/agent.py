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
import json
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import majordomo
from majordomo import dates

from . import reimbursement

HERE = Path(__file__).resolve().parent
CONFIG = Path("~/.config/majordomo/imperson.toml")
STATE = Path("~/.local/state/majordomo-imperson")
SERVER = "imperson"
SKILL_TOOLS = {"reimbursement": reimbursement.tools}
ALLOWED = ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]
DENIED = ["Bash", "Write", "Edit", "NotebookEdit", "Task"]
SEND_MODES = ("off", "test", "live")


@dataclass
class Turn:
    """One incoming message, as the skills' tools see it."""
    tenant: Path
    state: Path
    now: datetime
    send: str
    sender: str
    message: str
    space: str
    files: list[Path]
    post: Callable[[str, str, list[str]], None]
    git: Callable[[list[str]], None]


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
             tools: list, read_dirs: list[Path]) -> str | None:
    """One turn of a conversation through the Agent SDK; the session's id, to resume it at the next message."""
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, create_sdk_mcp_server, query

    async def turn() -> str | None:
        options = ClaudeAgentOptions(
            resume=session, cwd=cwd, system_prompt=system_prompt, skills=skills,
            setting_sources=["project"], strict_mcp_config=True,
            mcp_servers={SERVER: create_sdk_mcp_server(SERVER, tools=tools)},
            allowed_tools=[*ALLOWED, *(f"mcp__{SERVER}__{t.name}" for t in tools)],
            disallowed_tools=DENIED, permission_mode="dontAsk",
            hooks={"PreToolUse": [_confine(read_dirs)]}, max_turns=40)
        found = session
        async for m in query(prompt=prompt, options=options):
            if isinstance(m, ResultMessage):
                found = m.session_id or found
                if m.subtype != "success":
                    raise RuntimeError(f"the turn ended: {m.subtype}")
        return found
    return asyncio.run(turn())


def _git(args: list[str]) -> None:
    subprocess.run(["git", *args], check=True)


def whoami(cfg: dict) -> str:
    """The Chat id of the person whose token this is: People's ``people/<id>`` is Chat's ``users/<id>``."""
    from majordomo import api

    people = api.people_service(cfg)
    if people is None:
        raise SystemExit("the token cannot reach the People API, so the account cannot tell its own messages")
    me = people.people().get(resourceName="people/me", personFields="metadata").execute()["resourceName"]
    return me.replace("people/", "users/", 1)


class Account:
    def __init__(self, name: str, table: dict, *, chat=majordomo, talk=converse, git=_git, me=whoami,
                 now: datetime | None = None, state_root: Path = STATE):
        self.name, self.table, self.chat, self.talk, self.git = name, table, chat, talk, git
        self.send = table.get("send", "off")
        if self.send not in SEND_MODES:
            raise SystemExit(f"[account.{name}] send must be one of {', '.join(SEND_MODES)}")
        self.test_to = table.get("test_correspondent")
        if self.send == "test" and not self.test_to:
            raise SystemExit(f"[account.{name}] send = \"test\" needs test_correspondent")
        keys = Path(table["keys"]).expanduser()
        self.cfg = chat.load_config({"api": {"token_file": str(keys / "token.json"),
                                             "client_file": str(keys / "client_secret.json")}})
        self.me = me(self.cfg)
        self.tenant = Path(table["tenant"]).expanduser()
        self.skills = list(table.get("skills", []))
        self.state = state_root.expanduser() / name
        self.now = now or datetime.now(timezone.utc)
        self.out: list[str] = []
        saved = self.state / "state.json"
        self.saved = json.loads(saved.read_text()) if saved.exists() else {"mark": None, "handled": {}, "sessions": {}}

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
                f"The business's files are in {self.tenant}. Today is {self.now.date().isoformat()}.")

    def workspace(self) -> Path:
        """The session's working folder: it holds the skills and nothing that would load another project's instructions."""
        ws = self.state / "workspace"
        link, skills = ws / ".claude" / "skills", HERE / "skills"
        if link.is_symlink() and link.readlink() != skills:  # left by a checkout that has moved
            link.unlink()
        if not link.is_symlink():
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(skills)
        return ws

    def handle(self, space: str, row: dict, files: list[Path]) -> None:
        sender = row["sender_name"]
        turn = Turn(tenant=self.tenant, state=self.state, now=self.now, send=self.send, sender=sender,
                    message=row["name"], space=space, files=files,
                    post=lambda sp, text, fs: self.gate(space=sp, text=text, files=fs), git=self.git)
        tools = [reply_tool(lambda text, fs: self.gate(to=sender, text=text, files=fs))]
        for skill in self.skills:
            try:
                tools += SKILL_TOOLS.get(skill, lambda t: [])(turn)
            except Exception as exc:  # a skill whose files are missing leaves the conversation itself running
                print(f"imperson: {self.name}: the {skill} skill's tools are unavailable: {exc}", file=sys.stderr)
        when = dates.utc(row.get("create_time"))
        prompt = (f"From {row.get('sender_display') or sender} ({sender}), {when.isoformat() if when else ''}:\n"
                  f"{row.get('text') or '(no text)'}"
                  + "".join(f"\nFile saved from this message: {p}" for p in files))
        sessions = self.saved["sessions"]
        sessions[space] = self.talk(prompt, session=sessions.get(space), cwd=self.workspace(),
                                    system_prompt=self.system_prompt(), skills=self.skills, tools=tools,
                                    read_dirs=[self.state / "work", self.tenant / ".imperson"])
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
            with_files = {a["message_name"] for a in self.chat.attachments(self.cfg, space=space, start=since,
                                                                             cache_dir=cache)}
            for row in sorted(rows, key=lambda r: dates.utc(r.get("create_time")) or self.now):
                files: list[Path] = []
                try:
                    if row["name"] in with_files:
                        dest = self.state / "work" / row["name"].replace("/", "_")
                        dest.mkdir(parents=True, exist_ok=True)
                        saved = self.chat.attachments(self.cfg, message=row["name"], download_to=str(dest),
                                                      cache_dir=cache)
                        files = [Path(a["path"]) for a in saved if a.get("path")]
                    self.handle(space, row, files)
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
        accounts = tomllib.load(fh).get("account", {})
    for name, table in accounts.items():
        if args.account and name != args.account:
            continue
        try:
            for line in Account(name, table).run():
                print(line)
        except Exception as exc:  # one account's failure leaves the others to run
            print(f"imperson: {name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
