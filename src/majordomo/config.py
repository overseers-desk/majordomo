"""The config majordomo runs with, where the user's own files sit, the cache
directory, and the once-per-run notes.

A config has config.toml's shape, whether ``load_config`` is handed it as a
dict or reads it from a TOML file:

- ``[me]`` (the configured subject), ``[sieve]`` (blocked spaces and
  assignees), ``[api]`` (the OAuth token and client files),
  ``[identity.NAME]`` (a Chat app's service-account key, to send as with
  ``--as NAME``), and ``[db]`` (the MariaDB connection, the ``MYSQL_*`` keys).
- The user's own files sit in ``config_dir()``: ``config.toml``, which
  ``load_config()`` reads when handed nothing, and ``.env``, the MariaDB
  connection copied from the BI project, which the front doors merge into
  ``[db]`` (frontdoor.load); a read-only DB user swaps in later without any
  code change.
- ``$XDG_CACHE_HOME/majordomo/`` (``~/.cache/majordomo/`` when unset, on Linux
  and macOS alike), program-written: the People API person and Chat space
  objects majordomo has fetched (roster.py). Everything there is refetchable,
  so it is a cache.
"""

from __future__ import annotations

import copy
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .errors import MajordomoError


def config_dir() -> Path:
    """The user's own config directory, resolved per call, so importing
    majordomo reads nothing under the home directory."""
    return Path(os.path.expanduser("~/.config/majordomo"))


def config_toml() -> Path:
    return config_dir() / "config.toml"


def env_file() -> Path:
    return config_dir() / ".env"


def cache_dir() -> Path:
    """The XDG cache directory for majordomo, resolved per call so a test or a
    launcher can point it elsewhere through the environment."""
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base).expanduser() / "majordomo"


# Notes raised about a run, each held once until a front door drains them
# (the CLI onto stderr, the MCP server into its answer's ``notes``), after
# which the next call may raise them afresh. The core owns the wording and
# prints nothing; a front door only carries it.
_pending: list[str] = []


def note_once(message: str) -> None:
    if message not in _pending:
        _pending.append(message)


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
        raise MajordomoError(
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


# The tables majordomo reads from a config.
_TABLES = ("me", "sieve", "api", "identity", "db")
_SIEVE_LISTS = ("block_spaces", "block_assignees")


def load_config(source: str | os.PathLike | dict | None = None, identity: str | None = None) -> dict:
    """The config, in config.toml's shape. ``source`` is a dict, taken as it
    stands (a copy, so the caller's own is never changed); a path to a TOML
    file; or None for the user's ``config.toml``. A file's ``[api]`` files
    default to ``token.json`` and ``client_secret.json`` beside it, and ``~``
    in its paths is the home directory; a dict names its paths itself.
    ``identity`` names the ``[identity.NAME]`` the command acts as
    (``--as NAME``), checked here so a misspelt name stops the command before
    anything else runs."""
    # Parse the bound before anything else: a bad WORLD_AS_OF stops every
    # command, including ones that fetch no dates (design §5, hard failure
    # everywhere).
    world_as_of()
    if isinstance(source, dict):
        cfg = copy.deepcopy(source)
        where = "the config passed in"
        _check(cfg, where)
    else:
        path = config_toml() if source is None else Path(source)
        if not path.exists():
            raise MajordomoError(f"majordomo: no config at {path}")
        with open(path, "rb") as fh:
            cfg = tomllib.load(fh)
        where = str(path)
        _check(cfg, where)
        _file_paths(cfg, path.parent)
    if identity is not None:
        tables = cfg.get("identity") or {}
        if not (tables.get(identity) or {}).get("service_account_file"):
            known = ", ".join(sorted(n for n, t in tables.items() if t.get("service_account_file")))
            raise MajordomoError(
                f"majordomo: no [identity.{identity}] with a service_account_file in {where}"
                f" (configured: {known or 'none'})."
            )
        cfg["_identity"] = identity
    return cfg


def _check(cfg: dict, where: str) -> None:
    """Refuse a config whose tables are not tables or whose sieve lists are
    not lists of strings: a sieve list read as anything else would let a
    blocked space through. Keys majordomo does not know are kept."""
    for name in _TABLES:
        if cfg.get(name) is not None and not isinstance(cfg[name], dict):
            raise MajordomoError(f"majordomo: [{name}] in {where} must be a table.")
    for name, table in (cfg.get("identity") or {}).items():
        if not isinstance(table, dict):
            raise MajordomoError(f"majordomo: [identity.{name}] in {where} must be a table.")
    sieve = cfg.get("sieve") or {}
    for name in _SIEVE_LISTS:
        value = sieve.get(name)
        if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            raise MajordomoError(f"majordomo: [sieve].{name} in {where} must be a list of strings.")


def _file_paths(cfg: dict, directory: Path) -> None:
    api = cfg.setdefault("api", {})
    api["token_file"] = api.get("token_file") or str(directory / "token.json")
    api["client_file"] = api.get("client_file") or str(directory / "client_secret.json")
    for key, value in api.items():
        if isinstance(value, str):
            api[key] = os.path.expanduser(value)
    for table in (cfg.get("identity") or {}).values():
        if isinstance(table.get("service_account_file"), str):
            table["service_account_file"] = os.path.expanduser(table["service_account_file"])


def identity(config: dict) -> dict | None:
    """The ``[identity.NAME]`` table this command acts as, plus its ``name``;
    None when it acts as the personal login."""
    name = config.get("_identity")
    if name is None:
        return None
    return {"name": name, **config["identity"][name]}


def parse_env_file(path: str | os.PathLike) -> dict[str, str]:
    """The ``KEY=value`` lines of a ``.env`` file, quotes stripped."""
    env: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
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


def api_token_file(config: dict) -> str | None:
    return (config.get("api") or {}).get("token_file")


def api_client_file(config: dict) -> str | None:
    return (config.get("api") or {}).get("client_file")
