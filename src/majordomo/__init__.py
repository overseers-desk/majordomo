"""majordomo: read and report Google Chat task activity, and send messages.

The fast path reads an existing server-side MariaDB mirror of Google Chat
(``googlechat_*`` tables) and its already-decoded tasks (``coord_tasks``). The
direct-API read, its task decoder, and send live in api.py.

As a library it reads only what it is handed: ``load_config`` takes a dict
(or a TOML path) and the WORLD_AS_OF bound, and ``tasks``, ``messages`` and
``make_reader`` take the credentials and the name cache's directory.
"""

from importlib.metadata import PackageNotFoundError, version as _dist_version

from . import dates
from .api import send
from .config import drain_notes, load_config
from .errors import MajordomoError, NotFound, ScopeError
from .readers import make_reader, messages, tasks

__all__ = [
    "load_config",
    "make_reader",
    "tasks",
    "messages",
    "send",
    "drain_notes",
    "MajordomoError",
    "ScopeError",
    "NotFound",
]


def version() -> str:
    """The installed version, read from package metadata so pyproject.toml stays its one home."""
    try:
        return _dist_version("majordomo")
    except PackageNotFoundError:
        return "0+unknown"
