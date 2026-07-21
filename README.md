# majordomo

A command-line tool that reads Google Chat, reports task activity, and sends messages. The command line is the primary interface; an MCP server is a secondary interface for AI agents. Both are thin front doors over one shared core.

## What it does

When someone creates a task through Google Chat's "Create a task for @Person (via Tasks)", that task cannot be retrieved through the Google Tasks API; the only durable signal is the chat message itself (see [GOOGLE_CHAT_TASKS_LIMITATIONS.md](GOOGLE_CHAT_TASKS_LIMITATIONS.md)). majordomo reconstructs task activity from those messages and reports who holds which tasks across spaces over a date range.

- **Tasks** by assignee, space, and date; "assigned to me" and "assigned by me".
- **Spaces**, **people** (participants with message and task counts), and raw **messages** by space or thread.
- **Three modes, one shape.** The fast path reads an existing server-side cache of Chat (the [data model](DATA-MODEL.md)). `--live` is up-to-dateness: it serves the cache and tops it up from the Chat API with anything newer. `--nocache` reads the Chat API directly and decodes tasks itself, so the tool also works without the cache. Every result is tagged with its source; an unforced read uses the cache and falls back to the direct API automatically when the cache is unreachable.
- **Send** a message to a space, or a reply into a thread, as the logged-in account, with optional file attachments (`majordomo send`).
- **A privacy sieve** in the core drops blocked spaces (and assignees) before any caller (CLI or MCP) can see them; it refuses sends into blocked spaces the same way.
- **Output** as a rich console table, `--json`, or `--csv`.

## Install

The simplest cross-platform install is from PyPI:

```bash
pip install majordomo                  # CLI, reads the Chat cache
pip install "majordomo[api,mcp]"       # plus the live Chat API path and the MCP server
```

With uv, `uvx majordomo ...` runs it without installing and `uv tool install majordomo` installs it permanently.

Homebrew (macOS or Linux):

```bash
brew tap overseers-desk/od
brew install majordomo
```

Python 3.11+.

**Run directly, no virtualenv, with Ubuntu/Debian packages:**

```bash
sudo apt-get install python3-typer python3-rich python3-pymysql
# for the API path (majordomo login, --live top-up, --nocache, send) also:
sudo apt-get install python3-googleapi python3-google-auth python3-google-auth-oauthlib
```

Then run from the repo without installing the package:

```bash
PYTHONPATH=src python3 -m majordomo spaces
```

**Or install the package** (puts `majordomo` on your PATH):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[api,mcp]"       # drop the extras you don't need
majordomo --help
```

The MCP server (`majordomo mcp`) needs the `mcp` PyPI package, which is not in apt; install it through the virtualenv above (the `mcp` extra).

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

[api]                              # optional; defaults shown (OAuth for the API path)
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

## Authenticating (OAuth for the API path)

`majordomo login` opens a browser OAuth flow and writes `~/.config/majordomo/token.json` (the two Chat read scopes plus message create), used by `--live` (for the top-up), `--nocache`, and `send`. It needs a Desktop OAuth client with the Google Chat API enabled, saved as `client_secret.json` in the config directory. A token minted before send existed lacks its scope; `send` says so and re-running `majordomo login` fixes it.

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
majordomo send --space spaces/AAAA "On my way."
majordomo send --thread spaces/AAAA/messages/BBBB "Done, see the doc."
majordomo send --to alice@example.com "Lunch?"       # a person's existing 1:1 DM
majordomo send --space spaces/AAAA "Here it is." --attach ./report.pdf  # repeat --attach for several; text optional
majordomo mcp                       # run the MCP server (stdio)
```

- Source: default cache with an automatic direct-API fallback. `--cache` forces the cache; `--live` adds a freshness top-up from the API; `--nocache` reads the API directly.
- Window: `7d | 30d | month | year | all`, or `--since` / `--until` (ISO dates).
- Output: default console, `--json`, or `--csv`.

## Replay bounds: `WORLD_AS_OF`

`WORLD_AS_OF` is the office-wide replay bound (design: [WORLD_AS_OF.design.md](WORLD_AS_OF.design.md)): an ISO-8601 timestamp with a timezone offset, exported into the environment by a replay harness so a run sees the world as it stood at that instant.

```bash
WORLD_AS_OF='2026-07-12T17:07:00+10:00' majordomo tasks --window 7d
```

- **Unset**: normal operation, at no cost.
- **Set**: nothing dated after the bound is reported, on every source (cache, `--live`, `--nocache`) and through both front doors (CLI and MCP). Relative windows anchor to the bound, not to now: `7d` is the seven days before it, `month` the calendar month before the one containing it. A `--until` later than the bound is clamped down with a stderr note. Under a past bound `--live` degrades to the cache read (a top-up would fetch only what the bound excludes). The JSON/MCP envelope carries `world_as_of`, so a log proves the answer was bounded. `send` is refused while the bound is set: a bounded run is a replay, and a send would act in the real present.
- **Set but unparseable, or missing its timezone offset**: a hard error on every command, including ones that fetch no dates, because a silently ignored bound would produce a contaminated run that looks valid.

The bound is honest about what it cannot rewind. Space and user display names are current-state (the mirror keeps no history of prior names) and the output says so. A message edited after the bound carries its post-edit text, marked `edited_after_bound` on the API path where the edit is observable. A bound older than the oldest cached message earns a warning that the store does not reach the as-of instant. For replaying the past the cache is the higher-fidelity source: the mirror retains messages the API has since dropped through deletion.

## Not yet (deferred)

- **Task completion and stats.** Google Chat does not reliably carry task completion, so every task is reported as `open`; completion-rate reporting waits on a later signal.
- **Directory name resolution.** Names come from the chat message and the Chat API directly; a bare `users/<id>` with no name attached is shown as the id.

## License

See [LICENSE](LICENSE).
