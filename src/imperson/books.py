"""The business's books in MySQL: the files people send, the claims a skill makes of them, and the orders given.

A file's content is kept once however often it is sent, each arrival of it is an upload, and a claim is linked to
the uploads it rests on. Table names carry a prefix, so live books and test-mode books share a database unseen by each
other. Each operation is one transaction. Times are stored as naive UTC, since the driver drops a datetime's zone.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

VERSION = 1
KEYS = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")
TABLES = """
CREATE TABLE IF NOT EXISTS {p}schema (version INT NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS {p}files (id INT AUTO_INCREMENT PRIMARY KEY, sha256 CHAR(64) NOT NULL UNIQUE,
  media_type VARCHAR(127) NOT NULL, size INT NOT NULL, content LONGBLOB NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS {p}uploads (id INT AUTO_INCREMENT PRIMARY KEY, attachment VARCHAR(255) NOT NULL UNIQUE,
  file_id INT NULL, error VARCHAR(255) NULL, account VARCHAR(64) NOT NULL, message VARCHAR(128) NOT NULL,
  sender VARCHAR(64) NOT NULL, name VARCHAR(255) NOT NULL, media_type VARCHAR(127) NULL, received_at DATETIME NOT NULL,
  INDEX (message), FOREIGN KEY (file_id) REFERENCES {p}files(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS {p}claims (id INT AUTO_INCREMENT PRIMARY KEY, skill VARCHAR(64) NOT NULL,
  account VARCHAR(64) NOT NULL, person VARCHAR(64) NOT NULL, message VARCHAR(128) NOT NULL, handled_at DATETIME NOT NULL,
  scope VARCHAR(32) NOT NULL, outcome VARCHAR(32) NOT NULL, duplicate_of INT NULL, amount DECIMAL(12,2) NOT NULL,
  fields JSON NOT NULL, INDEX (skill, person, handled_at), INDEX (skill, message),
  FOREIGN KEY (duplicate_of) REFERENCES {p}claims(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS {p}claim_uploads (claim_id INT NOT NULL, upload_id INT NOT NULL,
  PRIMARY KEY (claim_id, upload_id), FOREIGN KEY (claim_id) REFERENCES {p}claims(id),
  FOREIGN KEY (upload_id) REFERENCES {p}uploads(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS {p}orders (id INT AUTO_INCREMENT PRIMARY KEY, skill VARCHAR(64) NOT NULL,
  account VARCHAR(64) NOT NULL, given_by VARCHAR(64) NOT NULL, message VARCHAR(128) NOT NULL, given_at DATETIME NOT NULL,
  kind VARCHAR(32) NOT NULL, person VARCHAR(64) NULL, claim_id INT NULL, amount DECIMAL(12,2) NULL,
  delta DECIMAL(12,2) NULL, words TEXT NOT NULL, INDEX (skill, person, given_at),
  FOREIGN KEY (claim_id) REFERENCES {p}claims(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def _stored(when: datetime) -> datetime:
    return when.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def _marks(values) -> str:
    return ", ".join(["%s"] * len(values))


def _read(row: dict) -> dict:
    out = {k: v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) else float(v) if isinstance(v, Decimal) else v
           for k, v in row.items()}
    if isinstance(out.get("fields"), (str, bytes)):  # MariaDB's JSON is LONGTEXT, and arrives as text
        out["fields"] = json.loads(out["fields"])
    return out


class Books:
    def __init__(self, settings: dict, prefix: str):
        self.settings, self.p, self.conn, self.ready = dict(settings), prefix, None, False

    def _connect(self):
        import pymysql.cursors
        if self.conn is not None:
            try:
                self.conn.ping()   # a turn can outlast a shared host's wait_timeout; pymysql's own reconnect is deprecated
                return self.conn
            except pymysql.err.Error:
                self.conn = None
        if missing := [k for k in KEYS if self.settings.get(k) is None]:
            raise RuntimeError(f"the books' settings have no {', '.join(missing)}")
        s = self.settings
        self.conn = pymysql.connect(host=s["MYSQL_HOST"], port=int(s.get("MYSQL_PORT", 3306)), user=s["MYSQL_USER"],
                                    password=s["MYSQL_PASSWORD"], database=s["MYSQL_DATABASE"],
                                    charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=False)
        return self.conn

    @contextmanager
    def _tx(self):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def _q(self, sql: str, values=()) -> list[dict]:
        with self._tx() as cur:
            cur.execute(sql, values)
            return [_read(r) for r in cur.fetchall()]

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def ensure_schema(self) -> None:
        if self.ready:
            return
        with self._tx() as cur:
            for statement in filter(str.strip, TABLES.format(p=self.p).split(";")):
                cur.execute(statement)
            cur.execute(f"SELECT version FROM {self.p}schema")
            found = [r["version"] for r in cur.fetchall()]
            if not found:
                cur.execute(f"INSERT INTO {self.p}schema (version) VALUES (%s)", (VERSION,))
            elif found != [VERSION]:
                raise RuntimeError(f"the books {self.p}* are at schema version {found}; this imperson knows {VERSION}")
        self.ready = True

    def add_upload(self, *, attachment: str, account: str, message: str, sender: str, name: str,
                   media_type: str | None, received_at: datetime, content: bytes | None, error: str | None) -> dict:
        """The upload's row, written once per attachment; a later arrival with content fills in a file the first
        could not fetch. ``identical_to`` is the earliest other upload of the same file, or None."""
        with self._tx() as cur:
            file_id = None
            if content is not None:
                digest = hashlib.sha256(content).hexdigest()
                cur.execute(f"INSERT INTO {self.p}files (sha256, media_type, size, content) VALUES (%s, %s, %s, %s) "
                            "ON DUPLICATE KEY UPDATE id = id",
                            (digest, (media_type or "application/octet-stream")[:127], len(content), content))
                cur.execute(f"SELECT id FROM {self.p}files WHERE sha256 = %s", (digest,))
                file_id = cur.fetchone()["id"]
            cur.execute(f"INSERT INTO {self.p}uploads (attachment, file_id, error, account, message, sender, name, media_type, "
                        "received_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                        "error = IF(VALUES(file_id) IS NULL, error, NULL), file_id = COALESCE(file_id, VALUES(file_id))",
                        (attachment, file_id, None if content is not None else (error or "not fetched")[:255], account,
                         message, sender, (name or "")[:255], media_type and media_type[:127], _stored(received_at)))
            cur.execute(f"SELECT * FROM {self.p}uploads WHERE attachment = %s", (attachment,))
            row = cur.fetchone()
            cur.execute(f"SELECT id, message, received_at FROM {self.p}uploads WHERE file_id = %s AND id <> %s "
                        "ORDER BY received_at, id LIMIT 1", (row["file_id"], row["id"]))   # a NULL file matches none
            same = cur.fetchone()
        return {**_read(row), "identical_to": _read(same) if same else None}

    def uploads(self, ids: list[int]) -> list[dict]:
        return self._q(f"SELECT * FROM {self.p}uploads WHERE id IN ({_marks(ids)}) ORDER BY id", ids) if ids else []

    def content(self, upload_id: int) -> bytes | None:
        found = self._q(f"SELECT f.content FROM {self.p}uploads u JOIN {self.p}files f ON f.id = u.file_id "
                        "WHERE u.id = %s", (upload_id,))
        return bytes(found[0]["content"]) if found else None

    def _where(self, skill: str, column: str, **match) -> tuple[str, list]:
        clauses, values = ["skill = %s"], [skill]
        for key, value in match.items():
            if value is not None:
                clauses.append(f"{column} >= %s" if key == "since" else f"{key} = %s")
                values.append(_stored(value) if key == "since" else value)
        return " AND ".join(clauses), values

    def claims(self, skill: str, *, person: str | None = None, since: datetime | None = None,
               message: str | None = None) -> list[dict]:
        where, values = self._where(skill, "handled_at", person=person, since=since, message=message)
        rows = self._q(f"SELECT c.*, (SELECT GROUP_CONCAT(CAST(upload_id AS CHAR) ORDER BY upload_id) FROM "
                       f"{self.p}claim_uploads WHERE claim_id = c.id) AS uploads FROM {self.p}claims c WHERE {where} "
                       "ORDER BY id", values)
        return [{**r, "uploads": [int(u) for u in (r["uploads"] or "").split(",") if u]} for r in rows]

    def add_claim(self, *, skill: str, account: str, person: str, message: str, handled_at: datetime, scope: str,
                  outcome: str, amount: float, fields: dict, uploads: list[int], duplicate_of: int | None = None) -> int:
        with self._tx() as cur:
            cur.execute(f"INSERT INTO {self.p}claims (skill, account, person, message, handled_at, scope, outcome, "
                        "duplicate_of, amount, fields) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (skill, account, person, message, _stored(handled_at), scope, outcome, duplicate_of, amount,
                         json.dumps(fields, ensure_ascii=False)))
            claim = cur.lastrowid
            cur.executemany(f"INSERT INTO {self.p}claim_uploads (claim_id, upload_id) VALUES (%s, %s)",
                            [(claim, u) for u in dict.fromkeys(uploads)])
        return claim

    def set_outcome(self, claim_id: int, outcome: str) -> None:
        self._q(f"UPDATE {self.p}claims SET outcome = %s WHERE id = %s", (outcome, claim_id))

    def add_order(self, *, skill: str, account: str, given_by: str, message: str, given_at: datetime, kind: str,
                  person: str | None, claim_id: int | None, amount: float | None, delta: float | None, words: str) -> int:
        with self._tx() as cur:
            cur.execute(f"INSERT INTO {self.p}orders (skill, account, given_by, message, given_at, kind, person, claim_id, "
                        "amount, delta, words) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (skill, account, given_by, message, _stored(given_at), kind, person, claim_id, amount, delta, words))
            return cur.lastrowid

    def orders(self, skill: str, *, person: str | None = None, since: datetime | None = None) -> list[dict]:
        where, values = self._where(skill, "given_at", person=person, since=since)
        return self._q(f"SELECT * FROM {self.p}orders WHERE {where} ORDER BY id", values)

    def standing(self, skill: str, person: str, since: datetime, outcomes: list[str]) -> tuple[float, float]:
        """Since ``since``: the person's claims under one of ``outcomes``, summed; and the sum of the deltas of their set_left
        orders."""
        when = _stored(since)
        spent = self._q(f"SELECT COALESCE(SUM(amount), 0) AS s FROM {self.p}claims WHERE skill = %s AND person = %s "
                        f"AND handled_at >= %s AND outcome IN ({_marks(outcomes)})",
                        [skill, person, when, *outcomes]) if outcomes else [{"s": 0.0}]
        moved = self._q(f"SELECT COALESCE(SUM(delta), 0) AS s FROM {self.p}orders WHERE skill = %s AND person = %s "
                        "AND kind = 'set_left' AND given_at >= %s", (skill, person, when))
        return spent[0]["s"], moved[0]["s"]
