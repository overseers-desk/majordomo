# majordomo

A command-line tool that reads Google Chat and reports task activity. The command line is the primary interface; an MCP server is a secondary interface for AI agents. Both are thin front doors over one shared core.

## What it does

When someone creates a task through Google Chat's "Create a task for @Person (via Tasks)", that task cannot be retrieved through the Google Tasks API; the only durable signal is the chat message itself (see [GOOGLE_CHAT_TASKS_LIMITATIONS.md](GOOGLE_CHAT_TASKS_LIMITATIONS.md)). majordomo reconstructs task activity from those messages and reports who holds which tasks across spaces over a date range.

- **Tasks** by assignee, space, and date; "assigned to me" and "assigned by me".
- **Spaces**, **people** (participants with message and task counts), and raw **messages** by space or thread.
- **Two sources, one shape.** A fast path reads an existing server-side cache of Chat (the [data model](DATA-MODEL.md)). A live path reads the Chat API directly and decodes tasks itself, so the tool also works without the cache. Every result is tagged with its source; the cache falls back to live automatically when it is unreachable.
- **A privacy sieve** in the core drops blocked spaces (and assignees) before any caller (CLI or MCP) can see them.
- **Output** as a rich console table, `--json`, or `--csv`.

## Install

```bash
pip install -e .            # base CLI (cache path)
pip install -e ".[live]"    # + live Google read and `login`
pip install -e ".[mcp]"     # + the MCP server
```

## Configuration

majordomo reads two files from `~/.config/majordomo/`:

`config.toml` (hand-edited):

```toml
[me]
user_id = "users/1234567890"      # your Chat id, for --to-me / --by-me
                                   # (find it with `majordomo people`)

[sieve]
block_spaces = ["spaces/AAAA"]     # never shown through any front door
block_assignees = ["users/9999"]   # drop these assignees from every report

[live]                             # optional; defaults shown
token_file = "~/.config/majordomo/token.json"
client_file = "~/.config/majordomo/client_secret.json"
```

`.env` (the cache database connection, when using the cache path):

```
MYSQL_HOST=…
MYSQL_PORT=3306
MYSQL_USER=…
MYSQL_PASSWORD=…
MYSQL_DATABASE=…
```

## Authenticating the live path

`majordomo login` opens a browser OAuth flow and writes `~/.config/majordomo/token.json` (read-only Chat scopes). It needs a Desktop OAuth client with the Google Chat API enabled, saved as `client_secret.json` in the config directory.

```bash
majordomo login
```

## Commands

```bash
majordomo spaces
majordomo people --window year
majordomo tasks --to-me --window month
majordomo tasks --assignee-name '*Alice*' --since 2026-01-01
majordomo messages --space spaces/AAAA --window 7d
majordomo messages --thread spaces/AAAA/messages/BBBB
majordomo mcp                       # run the MCP server (stdio)
```

- Source: default cache with live fallback; `--cache` or `--live` force one.
- Window: `7d | 30d | month | year | all`, or `--since` / `--until` (ISO dates).
- Output: default console, `--json`, or `--csv`.

## Not yet (deferred)

- **Task completion and stats.** Google Chat does not reliably carry task completion, so every task is reported as `open`; completion-rate reporting waits on a later signal.
- **Directory name resolution.** Names come from the chat message and the live API; a bare `users/<id>` with no name attached is shown as the id.

## License

See [LICENSE](LICENSE).
