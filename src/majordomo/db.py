"""Read-only access to the BI platform's Google Chat cache (MariaDB).

``connect`` opens one PyMySQL connection from ``~/.config/majordomo/.env`` (the
``ssl=None`` plaintext idiom the BI project uses against this host); ``query``
runs a parameterised read. The provenance of these rows is ``models.SOURCE_CACHE``.
The direct-API reader (api.py) is a separate stage with its own access path.
"""

from __future__ import annotations

import pymysql
from pymysql.cursors import DictCursor

from . import config


def connect(env: dict | None = None) -> pymysql.connections.Connection:
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
