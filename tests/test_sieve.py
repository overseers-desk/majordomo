"""The sieve must be un-bypassable: it binds a NOT IN clause AND post-filters,
so a blocked space is dropped even if the query layer returned it.
"""

import _shim  # noqa: F401  (path + runner)

from majordomo import db, reports, sieve


def test_clause_empty_is_noop():
    clause, params = sieve.clause([])
    assert clause == "1=1" and params == []


def test_clause_binds_each_blocked_id():
    clause, params = sieve.clause(["spaces/A", "spaces/B"], "t.space_name")
    assert clause == "t.space_name NOT IN (%s,%s)"
    assert params == ["spaces/A", "spaces/B"]


def test_filter_rows_drops_blocked():
    rows = [{"space_name": "spaces/OK"}, {"space_name": "spaces/NO"}]
    assert sieve.filter_rows(["spaces/NO"], rows) == [{"space_name": "spaces/OK"}]


def test_allows():
    assert sieve.allows(["spaces/NO"], "spaces/OK")
    assert not sieve.allows(["spaces/NO"], "spaces/NO")


def _patch_query(rows: list, captured: dict):
    def fake(conn, sql, params=()):
        captured["sql"] = sql
        captured["params"] = list(params)
        return rows
    return fake


def test_reports_spaces_defence_in_depth():
    blocked = ["spaces/BLOCK"]
    leaky = [{"space_name": "spaces/OK", "space_display": "OK", "space_type": "SPACE", "tasks": 1},
             {"space_name": "spaces/BLOCK", "space_display": "secret", "space_type": "SPACE", "tasks": 9}]
    captured: dict = {}
    orig = db.query
    db.query = _patch_query(leaky, captured)
    try:
        out = reports.spaces(None, blocked)
    finally:
        db.query = orig
    names = [r["space_name"] for r in out]
    assert "spaces/BLOCK" not in names           # post-filter caught the leak
    assert "NOT IN" in captured["sql"]            # clause was bound
    assert "spaces/BLOCK" in captured["params"]   # id passed as a parameter


def test_reports_tasks_defence_in_depth():
    blocked = ["spaces/BLOCK"]
    leaky = [{"space_name": "spaces/OK", "assignee": "a"},
             {"space_name": "spaces/BLOCK", "assignee": "b"}]
    captured: dict = {}
    orig = db.query
    db.query = _patch_query(leaky, captured)
    try:
        out = reports.tasks(None, blocked, start=None, end=None)
    finally:
        db.query = orig
    assert all(r["space_name"] != "spaces/BLOCK" for r in out)
    assert "NOT IN" in captured["sql"]


if __name__ == "__main__":
    _shim.run(dict(globals()))
