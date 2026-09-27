"""Install and keep current the majordomo command for Claude Code.

This installs a Claude Code *command* (the commands directory of the
configuration tree the session reads (``CLAUDE_CONFIG_DIR`` when set,
``~/.claude`` otherwise)), not
a skill. A user's skills directory is frequently a version-controlled, curated
collection, so a CLI writing into it would pollute that repository; the commands
directory is the conventional home for a tool to register itself. The ``COMMAND``
text below is the single source for the command's content. Every run keeps the
installed file equal to it: when the file is missing or differs from ``COMMAND``,
it is rewritten. "Current" means byte-for-byte equal to ``COMMAND``, so there is
no version stamp to maintain and no per-release judgement about whether the
command changed. A same-named skill, if the user keeps one, supersedes the
command and the refresh leaves it alone.

The ``description`` in the frontmatter is a discovery surface, not documentation:
its only job is to make the model reach for the command when the situation calls
for it. How to drive the CLI is the body below, which the model reads after it
decides to call.
"""

from __future__ import annotations

import os
from pathlib import Path

COMMAND_NAME = "majordomo"

# The command body and the single source of its content. The description names
# the question majordomo answers (who holds which Google Chat tasks) so an agent
# reaches for it when that question comes up; how to drive the CLI is the body,
# not the description.
COMMAND = """---
name: majordomo
description: Who holds which Google Chat tasks: tasks assigned by or to a person, plus message and task counts per space or person, over any date range. Reads the messages and files (video, photo, document) in a space, a thread, or a person's DM, naming people by name, email or id and spaces by name. Reports whether a space is a Google Workspace domain space or a consumer/personal one, and who owns it. Also sends a Google Chat message, with optional file attachments, to a space, a thread, or a person's DM.
allowed-tools: Bash
---

# majordomo

majordomo reports Google Chat task activity, and sends messages, over the `majordomo <command>` CLI. It reconstructs each task made through Chat's "Create a task for @Person (via Tasks)" from the chat messages, and reports who holds which tasks across spaces over a date range. Configuration lives in `~/.config/majordomo/` (`config.toml` for the subject and the privacy sieve, `.env` for the cache database). Add `--json` to any command for a `{"source", "count", "rows": [...]}` envelope; the `source` field tags each answer as `cache` or `live`.

A read uses the server-side cache by default and falls back to reading the Chat API directly when the cache is unreachable. `--cache` or `--nocache` before the command forces one source; `--nocache` needs a prior `majordomo login`, and `--cache` needs the cache driver (`majordomo[bi]`).

## Naming a person or a space

Wherever a command takes a person (`--person`, `--assignee`, `--to`), the value is one of: `users/<id>`; an email address; or a name. A name is matched, case-insensitively, against people's current names (from the Google People API) and, on the cache path, the spellings seen in task assignments and @-mentions, whole name first and then as a substring, and it must match exactly one person: a name matching several fails and lists them with their ids, and a name never seen fails and says so. Give the id or the email then. An email resolves through the People API's answers or the Chat API, and needs `majordomo login`.

Wherever a command takes a space (`--space`), the value is `spaces/<id>` or the space's display name, matched the same way.

Every report that shows a person (task assignees, message and file senders, space owners, `people`) names them through the People API, keeping what it fetched in `people.json` and `spaces.json` under `$XDG_CACHE_HOME/majordomo/` (`~/.cache/majordomo/` when unset); a person the API cannot name shows as `users/<id>`. When the saved login lacks a permission a command needs (as after an upgrade), majordomo opens Google's consent page itself and carries on once it is granted; where nobody can answer it (no display and no terminal), the command still answers, showing ids, and says to run `majordomo login`. Pass such a line on to the user rather than retrying.

## Tasks

```bash
majordomo tasks --to-me --window month
majordomo tasks --by-me --window year
majordomo tasks --assignee Alice --since 2026-01-01 --json > "$RESULTS"
majordomo tasks --space "Back Office" --until 2026-06-30
```

`--to-me` and `--by-me` resolve through `[me].user_id` in the config; `--assignee` names someone else. `--space` limits to one space. `status` is open, done or deleted, and the assignee the current holder, both replayed from Chat's own task messages (Completed, Re-opened, Deleted, Restored, Assigned, Changed assignee, Unassigned); a thread holding several tasks keeps them as created, since those messages name no task.

## Spaces and people

```bash
majordomo spaces
majordomo spaces --owner
majordomo people --window year
majordomo people --person Alice
```

`spaces` lists each space with its message and task counts; it hides spaces under one message by default (Google auto-creates an empty group per meeting), and `--minimal-messages=0` shows all. It also reports whether each space belongs to a Google Workspace domain or a consumer/personal account (from the space's `customer` field), read straight over the Chat API: this and `--owner` (below) render blank on a plain cache read, since the cache mirror holds neither. `--owner` additionally resolves who owns each space (the Membership with `role = ROLE_MANAGER`, which the Chat UI itself labels "Owner"), one extra API call per space, and names the owner. `people` lists everyone seen: their `users/<id>`, their name and the other spellings they have been called, their email and the DM space you share with them where known, with message and task counts. The identity columns are not windowed; the window bounds the counts only. `--person WHO` narrows to one person and is the check to make before using a name elsewhere; it is also how you find your own `users/<id>` for the config.

## Messages

```bash
majordomo messages --space spaces/AAAA --window 7d
majordomo messages --space "Back Office" --person Alice --window 30d
majordomo messages --person Alice --window 7d
majordomo messages --thread spaces/AAAA/messages/BBBB
```

Raw messages in one space, one thread (any message resource name in the thread), or with one person: `--person` alone reads your direct messages with them, both sides; with `--space` it keeps only their messages there. Rows are oldest-first and a capped answer keeps the newest.

## Attachments

```bash
majordomo attachments --space spaces/AAAA --window 30d
majordomo attachments --person Alice --window all
majordomo attachments --space "Back Office" --person Alice
majordomo attachments --message spaces/AAAA/messages/BBBB --download ./inbox
```

The files posted in one space, one thread, on one message (`--message`, the cheapest scope when you already have the message from a `messages` read), or by one person (`--person` alone: the files in your direct messages with them, both sides; with `--space`: only the files they posted there). Listing reports the filename, type and sender, oldest-first, and a capped answer keeps the newest; `--download <dir>` also writes each file into that existing directory under the name it was posted with, and each row then carries the `path` written. An existing file of that name is kept and named rather than overwritten. Always reads over the Chat API, the cache holding message text and not files, so this needs `majordomo login`; the read scope already covers it. A file held in Drive rather than Chat is listed but not downloaded, and says so.

## Send

```bash
majordomo send --space spaces/AAAA "On my way."
majordomo send --thread spaces/AAAA/messages/BBBB "Done, see the doc."
majordomo send --to alice@example.com "Lunch?"
majordomo send --to Alice "Lunch?"
majordomo send --space "Back Office" "Here it is." --attach ./report.pdf --attach ./chart.png
majordomo send --space spaces/AAAA --attach ./report.pdf
```

One target: `--space` posts to the space, `--thread` replies in a thread (any message resource name in it works), `--to` reaches a person's existing 1:1 DM (a person you have never DM'd is refused; majordomo does not open new DMs). `--attach <path>` uploads a local file as an attachment and repeats for several; the message text then becomes optional, so a file can go on its own. Sends as the logged-in account (attachments need no permission beyond sending). A blocked space answers "not found". While `WORLD_AS_OF` is set, a send is refused: a bounded run is a replay.

## Windows, output, source

`--window` takes `7d | 30d | month | year | all`, or set `--since` / `--until` with ISO dates instead. Output is a console table by default, `--json`, or `--csv`. Rows are capped (a stderr note says when); narrow `--window`/`--space` or raise `--limit`. `majordomo <command> --help` carries the remaining flags.

When the `WORLD_AS_OF` environment variable is set (ISO-8601 with timezone, a replay harness's as-of instant), majordomo honors it natively — nothing dated after the bound is reported, relative windows anchor to it, and the JSON envelope carries `world_as_of` — so do not add your own date filtering on top.
"""


def claude_dir() -> Path:
    """Return the Claude Code configuration directory this session reads.

    ``CLAUDE_CONFIG_DIR`` names it when set, ``~/.claude`` otherwise. One
    machine commonly carries several such directories, and a command written
    into the wrong one is invisible to the session that needed it.
    """
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def command_file() -> Path:
    return claude_dir() / "commands" / f"{COMMAND_NAME}.md"


def skill_dir() -> Path:
    """A skill of the same name, if the user keeps one, supersedes the command."""
    return claude_dir() / "skills" / COMMAND_NAME


def _superseded() -> bool:
    """True when Claude Code is absent, or a same-named skill supersedes the command."""
    return not claude_dir().exists() or skill_dir().exists()


def refresh() -> None:
    """Rewrite the command file when it is missing or differs from COMMAND.

    Idempotent and silent. Does nothing when Claude Code is not installed or a
    same-named skill supersedes the command. "Out of date" is content inequality
    with COMMAND, so no version field is needed.
    """
    if _superseded():
        return
    f = command_file()
    if f.exists() and f.read_text() == COMMAND:
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(COMMAND)


def install(echo) -> None:
    """(Re)write the majordomo command for Claude Code, reporting via ``echo``.

    The explicit form of ``refresh``: writes unconditionally and says where, so a
    user running ``majordomo install-claude-command`` gets feedback the silent
    per-run refresh does not give.
    """
    if _superseded():
        echo(
            "Skipped: Claude Code is not installed, or a same-named skill "
            "supersedes the command."
        )
        return
    f = command_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(COMMAND)
    echo(f"Installed: {f}")
