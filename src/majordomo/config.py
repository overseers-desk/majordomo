"""Load majordomo's two config files from ``~/.config/majordomo/``, name the
cache directory, and carry the once-per-run notes.

- ``config.toml`` — human-edited: ``[me]`` (the configured subject) and
  ``[sieve]`` (blocked spaces).
- ``.env`` — the MariaDB connection (``MYSQL_*``), copied from the BI project;
  a read-only DB user swaps in later without any code change.
- ``$XDG_CACHE_HOME/majordomo/`` (``~/.cache/majordomo/`` when unset, on Linux
  and macOS alike), program-written: the People API person and Chat space
  objects majordomo has fetched (roster.py). Everything there is refetchable,
  so it is a cache.
"""

from __future__ import annotations

import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path

CONFIG_DIR = Path(os.path.expanduser("~/.config/majordomo"))
CONFIG_TOML = CONFIG_DIR / "config.toml"
ENV_FILE = CONFIG_DIR / ".env"


def cache_dir() -> Path:
    """The XDG cache directory for majordomo, resolved per call so a test or a
    launcher can point it elsewhere through the environment."""
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base).expanduser() / "majordomo"


# Notes the core raises about a run: each printed to stderr once, and held
# until a front door whose caller reads no stderr (the MCP envelope) drains
# them into its answer, after which the next call may raise them afresh. The
# core owns the wording; a front door only carries it.
_pending: list[str] = []


def note_once(message: str) -> None:
    import sys

    if message in _pending:
        return
    _pending.append(message)
    print(message, file=sys.stderr)


def drain_notes() -> list[str]:
    """The notes raised since the last drain, oldest first."""
    out = list(_pending)
    _pending.clear()
    return out

# The office-wide replay bound (WORLD_AS_OF.design.md): when set, nothing dated
# after this instant may leave majordomo. Read from the environment on every
# call. A long-running MCP server honors the environment its launcher set and
# fails per call, not at handshake.
WORLD_AS_OF_ENV = "WORLD_AS_OF"

# The once-per-run honesty flag (design §3 rule 1): metadata the store keeps no
# history for is served as it stands now, and the output says so.
WORLD_CURRENT_STATE_NOTE = (
    "space and user display names are current-state, not rewound to WORLD_AS_OF"
)


def world_as_of() -> datetime | None:
    """The WORLD_AS_OF bound as a naive-UTC datetime, or None when unset.

    The variable is ISO-8601 with a timezone offset. Set but unparseable (or
    timezone-naive) is a hard failure on every code path: a silently ignored
    bound produces a contaminated run that looks valid. The store and
    ``dates.resolve`` work in naive UTC, so the offset is converted then dropped.
    """
    raw = os.environ.get(WORLD_AS_OF_ENV)
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise SystemExit(
            f"majordomo: WORLD_AS_OF={raw!r} must be an ISO-8601 timestamp with a "
            "timezone offset (e.g. 2026-07-12T17:07:00+10:00); refusing to run "
            "with an unenforceable bound."
        )
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def world_clamp(end: datetime | None) -> datetime | None:
    """Clamp an end-exclusive upper bound to WORLD_AS_OF; identity when unset.

    The one clamp rule (design §2): ``end = min(end or WORLD_AS_OF, WORLD_AS_OF)``
    wherever an end bound exists. Enforcement lives inside each backend (the
    reader seam), so the cache->nocache fallback cannot leak.
    """
    bound = world_as_of()
    if bound is None:
        return end
    if end is None:
        return bound
    if end.tzinfo is not None:
        end = end.astimezone(timezone.utc).replace(tzinfo=None)
    return min(end, bound)


def load_config() -> dict:
    # Parse the bound before anything else: a bad WORLD_AS_OF stops every
    # command, including ones that fetch no dates (design §5, hard failure
    # everywhere).
    world_as_of()
    if not CONFIG_TOML.exists():
        raise SystemExit(f"majordomo: no config at {CONFIG_TOML}")
    with open(CONFIG_TOML, "rb") as fh:
        return tomllib.load(fh)


def load_env() -> dict:
    if not ENV_FILE.exists():
        raise SystemExit(f"majordomo: no database .env at {ENV_FILE}")
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def me_user_id(config: dict) -> str | None:
    """The configured subject's ``users/<id>`` resource name, or None."""
    return (config.get("me") or {}).get("user_id")


def me_google_id(config: dict) -> str | None:
    """The configured subject's email. Cannot drive filters in v1 (no email
    data in the mirror); kept for the later API-resolution path."""
    return (config.get("me") or {}).get("google_id")


def require_user_id(config: dict) -> str:
    """The configured subject's ``users/<id>``, or raise ValueError with guidance.

    The email (``google_id``) is not resolved to an id here; ``user_id`` must
    be set explicitly. Both front doors call this, so the
    rule lives in one place.
    """
    uid = me_user_id(config)
    if uid:
        return uid
    email = me_google_id(config)
    hint = f" (config has [me].google_id={email!r}, an email, which is not resolved here)" if email else ""
    raise ValueError(
        f"--to-me/--by-me need [me].user_id in config{hint}. "
        "Run `majordomo people` to find your users/<id>, then add it as [me].user_id."
    )


def block_spaces(config: dict) -> list[str]:
    return list((config.get("sieve") or {}).get("block_spaces") or [])


def block_assignees(config: dict) -> list[str]:
    """Assignees (users/<id> or prose @name) to drop from every output."""
    return list((config.get("sieve") or {}).get("block_assignees") or [])


def api_token_file(config: dict) -> str:
    return (config.get("api") or {}).get("token_file") or str(CONFIG_DIR / "token.json")


def api_client_file(config: dict) -> str:
    return (config.get("api") or {}).get("client_file") or str(CONFIG_DIR / "client_secret.json")
