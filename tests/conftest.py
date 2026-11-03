"""A MariaDB for the tests that need imperson's books: the server IMPERSON_TEST_MYSQL names, or one run in Docker.

IMPERSON_TEST_MYSQL is a URL, mysql://user:password@host:port/database. Without it the session starts mariadb:10.5
in Docker, the first time a test asks for the books, and removes it at the end; a test that never asks starts
nothing. The tests' tables carry a prefix of their own, so a server that also holds live or test-mode books keeps them.
"""

import os
import subprocess
import time
import uuid
from urllib.parse import unquote, urlparse

import pytest

IMAGE = "mariadb:10.5"
PREFIX = "imperson_pytest_"
NEEDED = ("the ledger tests need a MariaDB: start Docker, or set IMPERSON_TEST_MYSQL to a server's URL "
          "(mysql://user:password@host:port/database)")


def _connect(settings):
    import pymysql

    return pymysql.connect(host=settings["MYSQL_HOST"], port=settings["MYSQL_PORT"], user=settings["MYSQL_USER"],
                           password=settings["MYSQL_PASSWORD"], database=settings["MYSQL_DATABASE"], connect_timeout=3)


def _docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(scope="session")
def mysql():
    url = os.environ.get("IMPERSON_TEST_MYSQL")
    if url:
        u = urlparse(url)
        yield {"MYSQL_HOST": u.hostname, "MYSQL_PORT": u.port or 3306, "MYSQL_USER": unquote(u.username or ""),
               "MYSQL_PASSWORD": unquote(u.password or ""), "MYSQL_DATABASE": u.path.lstrip("/")}
        return
    password = uuid.uuid4().hex
    try:
        container = _docker("run", "-d", "--rm", "--tmpfs", "/var/lib/mysql", "-e", f"MARIADB_ROOT_PASSWORD={password}",
                            "-e", "MARIADB_DATABASE=imperson_test", "-p", "127.0.0.1:0:3306", IMAGE)   # a data directory in memory starts in seconds
    except (OSError, subprocess.CalledProcessError) as exc:
        container, why = None, getattr(exc, "stderr", None) or exc
    if container is None:
        pytest.fail(f"{NEEDED}. Docker said: {why}", pytrace=False)
    try:
        port = int(_docker("port", container, "3306/tcp").splitlines()[0].rsplit(":", 1)[1])
        settings = {"MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": port, "MYSQL_USER": "root",
                    "MYSQL_PASSWORD": password, "MYSQL_DATABASE": "imperson_test"}
        deadline = time.monotonic() + 90
        while True:
            try:
                _connect(settings).close()
                break
            except Exception as exc:
                if time.monotonic() > deadline:
                    pytest.fail(f"the MariaDB in Docker took no connection within 90 seconds: {exc}", pytrace=False)
                time.sleep(0.5)
        yield settings
    finally:
        # the server is shut down from inside first, as some hosts' Docker cannot signal a container to stop
        subprocess.run(["docker", "exec", container, "mariadb-admin", "-uroot", f"-p{password}", "shutdown"],
                       capture_output=True)
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)


@pytest.fixture
def books(mysql):
    """Books on the test server with every table empty."""
    from imperson.books import Books

    found = Books(mysql, PREFIX)
    found.ensure_schema()
    conn = _connect(mysql)
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")    # a claim may name an earlier one as its duplicate
        for table in ("claim_uploads", "orders", "claims", "uploads", "files"):
            cur.execute(f"DELETE FROM {PREFIX}{table}")   # TRUNCATE remakes the table's file, six times slower on disk
            cur.execute(f"ALTER TABLE {PREFIX}{table} AUTO_INCREMENT = 1")
    conn.commit()
    conn.close()
    yield found
    found.close()
