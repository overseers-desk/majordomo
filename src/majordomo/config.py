"""Load majordomo's two config files from ``~/.config/majordomo/``.

- ``config.toml`` — human-edited: ``[me]`` (the configured subject) and
  ``[sieve]`` (blocked spaces).
- ``.env`` — the MariaDB connection (``MYSQL_*``), copied from the BI project;
  a read-only DB user swaps in later without any code change.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

CONFIG_DIR = Path(os.path.expanduser("~/.config/majordomo"))
CONFIG_TOML = CONFIG_DIR / "config.toml"
ENV_FILE = CONFIG_DIR / ".env"


def load_config() -> dict:
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

    v1 has no People API, so the email (``google_id``) cannot be resolved to
    an id; ``user_id`` must be set explicitly. Both front doors call this, so the
    rule lives in one place.
    """
    uid = me_user_id(config)
    if uid:
        return uid
    email = me_google_id(config)
    hint = f" (config has [me].google_id={email!r}, an email, which v1 cannot resolve)" if email else ""
    raise ValueError(
        f"--to-me/--by-me need [me].user_id in config{hint}. "
        "Run `majordomo people` to find your users/<id>, then add it as [me].user_id."
    )


def block_spaces(config: dict) -> list[str]:
    return list((config.get("sieve") or {}).get("block_spaces") or [])


def block_assignees(config: dict) -> list[str]:
    """Assignees (users/<id> or prose @name) to drop from every output."""
    return list((config.get("sieve") or {}).get("block_assignees") or [])


def nocache_token_file(config: dict) -> str:
    return (config.get("nocache") or {}).get("token_file") or str(CONFIG_DIR / "token.json")


def nocache_client_file(config: dict) -> str:
    return (config.get("nocache") or {}).get("client_file") or str(CONFIG_DIR / "client_secret.json")
