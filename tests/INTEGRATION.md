# Deployment-readiness gate

`tests/integration.sh` is the last stage before deployment. It is **not** a
software-strength test: the unit suite already proves the code logic against a fake
Chat service (`tests/test_nocache_reader.py`, "no creds/network"). This script answers
a different question. Is *this machine*, with *this configuration* and *these
credentials*, ready to serve a real user?

The judging rule is strict. Every readiness check is `PASS` or `FAIL`; there is no
`SKIP` and no `WARN`. A path that cannot be exercised is a path that is not ready,
so it fails. An invalid OAuth client, a dead token, a cache DB server that is down,
an MCP front door that will not start, a missing config file, a test space that
does not exist: each stops a real user, so each is a hard `FAIL`. The script is
read-only; no command writes to a space.

## The one thing that is not a verdict: cannot-run conditions

If the test machine loses its own network (DNS for Google or the DB host stops
resolving), or Google answers `429` ("retry later"), the gate cannot complete, so
it **aborts with exit 2** instead of emitting false failures. The distinction is
drawn per command: a command that fails while the network is up and quota is intact
is a real `FAIL`; a command that fails on a network drop or a quota 429 aborts. A
DB server that is down but whose host still resolves stays a `FAIL`, because that
is a deployment problem, not a harness one.

Exit status:

| code | meaning                                            |
|------|----------------------------------------------------|
| 0    | ready (every check passed)                         |
| 1    | not ready (one or more `FAIL`)                     |
| 2    | cannot run, retry (network loss or read-quota 429) |

## What is checked, and why each blocks deployment

**Configuration** (`~/.config/majordomo/`, override the dir with `MAJORDOMO_CONFIG_DIR`):

- `config.toml` present and valid TOML.
- `[me].user_id` set (without it `--to-me`/`--by-me` exit 2).
- `.env` present (the cache DB connection).
- `client_secret.json` present and carrying a `client_id`.
- `token.json` present and carrying a `refresh_token`.

**Front doors and guards:**

- `install-claude-command` exits 0.
- `--live --cache` together is rejected (exit 2); a rejection is the pass.
- `mcp` starts. It is the pip-only front door, shipped in the **venv** (with the
  `mcp` extra), not the deb, so the gate boots it through the venv interpreter
  (`$MAJORDOMO_VENV/bin/python -m majordomo mcp`, default `./.venv`). An absent venv,
  a missing install, or a crash is a `FAIL`. A clean EOF exit or the 3s timeout both
  count as a healthy boot.

**Credentials** (the `login` proxy): `login` is an interactive browser flow and
cannot run headless, but its deployment-relevant product is a token the direct-API
path uses. `majordomo --nocache spaces` forces that path, so a dead or revoked
client fails here as `invalid_client`. (`--live` would not prove auth: it is
cache-backed and only calls the API to top up when the cache lags.)

**Fixtures exist on the API:** `TEST_SPACE` and `TEST_DM` must appear in
`majordomo --nocache spaces`. A missing fixture means a wrong id, lost access, or
dead auth, each a `FAIL`.

**Read matrices:** the full window/format cross-product runs on the local backends
(`--cache`, auto). The API-touching leg covers the two source modes that hit Google,
scoped so neither does a global scan:

- `spaces`: default, `--minimal-messages 0`, `--json`, `--csv` (cache/auto).
- `people`: every window (`7d 30d month year all`), `--since/--until`, `--json`, `--csv` (cache/auto); on `--live` it is cache-backed (identities are stable), so it is exercised once as a fast no-API check.
- `tasks`: default, `--to-me`, `--by-me`, `--assignee-name`, `--space`, `--window all`, `--limit`, `--json`, `--csv` (cache/auto); on `--live`, `--space` (top-up of one space) and one unscoped `--window 7d` (top-up bounded to the recently-active spaces, never all ~191); on `--nocache`, `--space` (direct, scoped).
- `messages`: `--space` (group and DM), `--window all`, `--json`, `--csv`, and `--thread` from a lifted message name, on both `--live` (top-up) and `--nocache` (direct), always scoped to a fixture.

**Daily-DM freshness:** the subject DMs daily, so the newest message in `TEST_DM`
must be within `MAJORDOMO_DM_FRESH_DAYS` (default 1). A stale newest message means
the read pipeline is broken even when commands exit 0, so staleness is a `FAIL`.

## Fixtures

| var                    | default              | meaning                          |
|------------------------|----------------------|----------------------------------|
| `MAJORDOMO_TEST_SPACE` | `spaces/AAQAGiUqUAU` | a group space with messages      |
| `MAJORDOMO_TEST_DM`    | `spaces/jP4cXEAAAAE` | a 1:1 DM the subject uses daily  |

The Google Chat web URL `https://chat.google.com/app/chat/<id>` carries `<id>`, and
the API resource name is `spaces/<id>`.

## Running

```
tests/integration.sh                                    # against the installed `majordomo`
MAJORDOMO='python3 -m majordomo' tests/integration.sh   # against the source tree
MAJORDOMO_TEST_SPACE=spaces/XXXX tests/integration.sh   # override a fixture
```

Read the exit code: 0 ready, 1 not ready, 2 retry.
