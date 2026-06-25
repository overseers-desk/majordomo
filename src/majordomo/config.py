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
    data in the mirror); kept for the later live-resolution path."""
    return (config.get("me") or {}).get("google_id")


def block_spaces(config: dict) -> list[str]:
    return list((config.get("sieve") or {}).get("block_spaces") or [])
