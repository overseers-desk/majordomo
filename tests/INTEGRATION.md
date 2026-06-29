# Integration smoke

`tests/integration.sh` drives every majordomo command against a real environment
and checks each exits without error. It is the complement to the unit suite, which
runs the readers against an injected fake Chat service (`tests/test_live_reader.py`, "no
creds/network") and so never touches OAuth, the database, or the live API. This
script does. It is read-only: no command writes to any space.

## What "doesn't error" means here

Each invocation is judged by exit code. `PASS` is exit 0. `FAIL` is a command that
should have succeeded returning non-zero. `SKIP` is a precondition unmet, so the
command could not be tested (cache DB down, live token invalid, MCP extra absent,
interactive flow). `WARN` is a command that ran while a fixture looks wrong (see DM
freshness). A skip is not a pass: it records that a path went unexercised.

## Backends

Every read command runs under three sources, each gated by a preflight probe:

| source     | flag       | precondition                                  |
|------------|------------|-----------------------------------------------|
| cache      | `--cache`  | MariaDB mirror reachable via `~/.config/majordomo/.env` |
| auto       | (none)     | cache, else live fallback                      |
| live       | `--live`   | valid `~/.config/majordomo/token.json`         |

When a source's probe fails, its whole matrix is skipped with the reason. This
keeps a broken token from drowning the report in identical failures.

## Fixtures

Two spaces the subject can read, set at the top of the script or by env var:

| var                    | default              | meaning                          |
|------------------------|----------------------|----------------------------------|
| `MAJORDOMO_TEST_SPACE` | `spaces/AAQAGiUqUAU` | a group space with messages      |
| `MAJORDOMO_TEST_DM`    | `spaces/jP4cXEAAAAE` | a 1:1 DM the subject uses daily  |

The Google Chat web URL `https://chat.google.com/app/chat/<id>` carries `<id>`,
and the API resource name is `spaces/<id>`. That URL-to-resource mapping is an
assumption, so the live preflight checks both ids appear in `majordomo --live
spaces` and emits a `WARN` if either is missing; a wrong id then surfaces rather
than passing silently as empty results.

The DM is used daily, so after the live matrix the script reads its newest message
and `WARN`s when that message is older than `MAJORDOMO_DM_FRESH_DAYS` (default 1):
a stale newest message points at a broken ingest, not a code fault.

## Coverage

Backend-independent (run once):

- `install-claude-command`, expecting exit 0.
- `--live --cache` together, which must be **rejected** (exit 2); the script counts
  the rejection as the pass.
- `login` is **skipped**, being interactive (it opens a browser). Verify by hand:
  run `majordomo login`, complete the consent, confirm `token written to …`.
- `mcp` gets a boot smoke: it starts the stdio server with closed stdin under a 3s
  timeout, and a clean EOF exit or the timeout both count as "booted without
  crashing". The MCP tools (`spaces`/`people`/`tasks`/`messages`) reach the same
  reader seam as the CLI, so the CLI matrix below covers their behaviour; a full
  MCP-client test is a deeper follow-up.

Per backend (`--cache`, auto, `--live`):

- `spaces`: default, `--minimal-messages 0`, `--json`, `--csv`.
- `people`: every window (`7d 30d month year all`), `--since/--until`, `--json`, `--csv`.
- `tasks`: default, `--to-me`, `--by-me`, `--assignee-name`, `--space`, `--window
  all`, `--limit`, `--json`, `--csv`.
- `messages`: `--space` (group and DM), `--window all`, `--json`, `--csv`, and
  `--thread` fed by a message name lifted from the space (skipped when the space is
  empty).

`--to-me`/`--by-me` read `[me].user_id` from config; when it is unset the command
exits 2 with guidance, which would show as a `FAIL` here. Set `[me].user_id` (run
`majordomo people` to find it) before relying on those two lines.

## Running

```
tests/integration.sh                       # against the installed `majordomo`
MAJORDOMO='python3 -m majordomo' tests/integration.sh   # against the source tree
MAJORDOMO_TEST_SPACE=spaces/XXXX tests/integration.sh   # override a fixture
```

Exit status is 0 only when `FAIL` is 0. `SKIP` and `WARN` do not fail the run, so
read the summary line, not just the exit code.
