"""Parity features: glob->LIKE, assignee blocking, CSV output, thread filter."""

import _shim  # noqa: F401

import io
import sys

from majordomo import db, models, output, reports, sieve


def test_glob_to_like():
    assert reports._glob_to_like("*Alice*") == "%Alice%"
    assert reports._glob_to_like("a?c") == "a_c"
    assert reports._glob_to_like("100%_x") == "100\\%\\_x"


def test_filter_assignees_by_id_or_name():
    rows = [{"assignee_user_name": "users/1", "assignee": "Alice"},
            {"assignee_user_name": "users/2", "assignee": "Bob"}]
    assert [r["assignee"] for r in sieve.filter_assignees(["users/1"], rows)] == ["Bob"]
    assert [r["assignee"] for r in sieve.filter_assignees(["Bob"], rows)] == ["Alice"]
    assert len(sieve.filter_assignees([], rows)) == 2


def test_filter_assignees_people_keys():
    rows = [{"user_id": "users/1", "display": "Alice"}, {"user_id": "users/2", "display": None}]
    out = sieve.filter_assignees(["users/2"], rows, id_key="user_id", name_key="display")
    assert [r["user_id"] for r in out] == ["users/1"]


def test_csv_output():
    rows = [{"a": 1, "b": None}, {"a": 2, "b": "x"}]
    cols = [("A", "a"), ("B", "b")]
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        output.emit(rows, cols, "cache", output_csv=True)
    finally:
        sys.stdout = old
    lines = buf.getvalue().splitlines()
    assert lines[0] == "A,B"
    assert lines[1] == "1,"
    assert lines[2] == "2,x"


def test_messages_thread_filter_sql():
    captured = {}

    def fake(conn, sql, params=()):
        captured["sql"], captured["params"] = sql, list(params)
        return []

    orig = db.query
    db.query = fake
    try:
        reports.messages(None, [], thread="spaces/X/messages/ABC.DEF")
    finally:
        db.query = orig
    assert "m.name LIKE" in captured["sql"]
    assert "spaces/X/messages/ABC.%" in captured["params"]


def test_messages_needs_space_or_thread():
    try:
        reports.messages(None, [])
    except SystemExit:
        return
    raise AssertionError("messages with neither space nor thread should exit")


if __name__ == "__main__":
    _shim.run(dict(globals()))
