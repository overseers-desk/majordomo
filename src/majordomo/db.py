"""Read-only access to the BI platform's Google Chat cache (MariaDB).

``connect`` opens one PyMySQL connection from the ``MYSQL_*`` settings it is
handed, a config's ``[db]``, in the BI project's plaintext ``ssl=None`` idiom
for its database; ``query`` runs a parameterised read. The provenance of these
rows is ``models.SOURCE_CACHE``. The direct-API reader (api.py) is a separate
stage with its own access path.

The driver is imported inside ``connect`` because the cache is the optional
half: majordomo reads Google directly without it, and a caller who never
touches the cache should not need the database driver installed to start.
"""

from __future__ import annotations

from .errors import MajordomoError

# The settings a connection cannot do without; MYSQL_PORT defaults to 3306.
_REQUIRED = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")


def _driver():
    """An absent driver is one way for the cache to be unavailable, and the
    reader seam turns any such failure into the fallback to reading Google
    directly.
    """
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:
        raise RuntimeError(
            "the database driver is not installed; pip install 'majordomo[bi]'"
        ) from exc
    return pymysql, DictCursor


def connect(settings: dict):
    pymysql, DictCursor = _driver()
    missing = [key for key in _REQUIRED if key not in settings]
    if missing:
        raise MajordomoError(
            f"majordomo: no cache database: [db] (or the .env beside config.toml) lacks {', '.join(missing)}"
        )
    return pymysql.connect(
        host=settings["MYSQL_HOST"],
        port=int(settings.get("MYSQL_PORT", "3306")),
        user=settings["MYSQL_USER"],
        password=settings["MYSQL_PASSWORD"],
        database=settings["MYSQL_DATABASE"],
        cursorclass=DictCursor,
        ssl=None,
    )


def query(conn, sql: str, params: tuple | list = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())
