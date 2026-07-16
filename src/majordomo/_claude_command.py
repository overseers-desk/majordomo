"""Install and keep current the majordomo command for Claude Code.

This installs a Claude Code *command* (``~/.claude/commands/majordomo.md``), not
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

from pathlib import Path

COMMAND_NAME = "majordomo"

# The command body and the single source of its content. The description names
# the question majordomo answers (who holds which Google Chat tasks) so an agent
# reaches for it when that question comes up; how to drive the CLI is the body,
# not the description.
COMMAND = """---
name: majordomo
description: Who holds which Google Chat tasks: tasks assigned by or to a person, plus message and task counts per space or person, over any date range. Covers Chat-created tasks the Tasks API cannot return.
allowed-tools: Bash
---

# majordomo

majordomo reports Google Chat task activity over the `majordomo <command>` CLI. A task created through Chat's "Create a task for @Person (via Tasks)" is not retrievable through the Google Tasks API; majordomo reconstructs it from the chat message instead, and reports who holds which tasks across spaces over a date range. Configuration lives in `~/.config/majordomo/` (`config.toml` for the subject and the privacy sieve, `.env` for the cache database). Add `--json` to any command for a `{"source", "count", "rows": [...]}` envelope; the `source` field tags each answer as `cache` or `live`.

A read uses the server-side cache by default and falls back to reading the Chat API directly when the cache is unreachable. `--cache` or `--nocache` before the command forces one source; `--nocache` needs the api extra and a prior `majordomo login`.

## Tasks

```bash
majordomo tasks --to-me --window month
majordomo tasks --by-me --window year
majordomo tasks --assignee-name '*Alice*' --since 2026-01-01 --json > "$RESULTS"
majordomo tasks --space spaces/AAAA --until 2026-06-30
```

`--to-me` and `--by-me` resolve through `[me].user_id` in the config; `--assignee users/<id>` or `--assignee-name '<glob>'` name someone else. `--space spaces/<id>` limits to one space. Every task reads as `open`: Chat does not reliably carry completion.

## Spaces and people

```bash
majordomo spaces
majordomo people --window year
```

`spaces` lists each space with its message and task counts; it hides spaces under one message by default (Google auto-creates an empty group per meeting), and `--minimal-messages=0` shows all. `people` lists participants (senders and assignees) with message and task counts, and is how you find your own `users/<id>` for the config.

## Messages

```bash
majordomo messages --space spaces/AAAA --window 7d
majordomo messages --thread spaces/AAAA/messages/BBBB
```

Raw messages in one space, or one thread (any message resource name in the thread).

## Windows, output, source

`--window` takes `7d | 30d | month | year | all`, or set `--since` / `--until` with ISO dates instead. Output is a console table by default, `--json`, or `--csv`. Rows are capped (a stderr note says when); narrow `--window`/`--space` or raise `--limit`. `majordomo <command> --help` carries the remaining flags.

When the `WORLD_AS_OF` environment variable is set (ISO-8601 with timezone, a replay harness's as-of instant), majordomo honors it natively — nothing dated after the bound is reported, relative windows anchor to it, and the JSON envelope carries `world_as_of` — so do not add your own date filtering on top.
"""


def command_file() -> Path:
    return Path.home() / ".claude" / "commands" / f"{COMMAND_NAME}.md"


def skill_dir() -> Path:
    """A skill of the same name, if the user keeps one, supersedes the command."""
    return Path.home() / ".claude" / "skills" / COMMAND_NAME


def _superseded() -> bool:
    """True when Claude Code is absent, or a same-named skill supersedes the command."""
    return not (Path.home() / ".claude").exists() or skill_dir().exists()


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
