"""Read-only access to the BI platform's Google Chat cache (MariaDB).

``connect`` opens one PyMySQL connection from ``~/.config/majordomo/.env`` (the
``ssl=None`` plaintext idiom the BI project uses against this host); ``query``
runs a parameterised read. The provenance of these rows is ``models.SOURCE_CACHE``.
The direct-API reader (api.py) is a separate stage with its own access path.

The driver is imported inside ``connect`` because the cache is the optional
half: majordomo reads Google directly without it, and a caller who never
touches the cache should not need the database driver installed to start.
"""

from __future__ import annotations

from . import config


def _driver():
    """The MySQL driver, or an ordinary exception naming how to get it.

    Ordinary, not SystemExit: an absent driver is one way for the cache to be
    unavailable, and the reader seam turns any such failure into the fallback
    to reading Google directly. Exiting here would kill that fallback.
    """
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:
        raise RuntimeError(
            "the database driver is not installed; pip install 'majordomo[bi]'"
        ) from exc
    return pymysql, DictCursor


def connect(env: dict | None = None):
    pymysql, DictCursor = _driver()
    env = env or config.load_env()
    return pymysql.connect(
        host=env["MYSQL_HOST"],
        port=int(env.get("MYSQL_PORT", "3306")),
        user=env["MYSQL_USER"],
        password=env["MYSQL_PASSWORD"],
        database=env["MYSQL_DATABASE"],
        cursorclass=DictCursor,
        ssl=None,
    )


def query(conn, sql: str, params: tuple | list = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())
