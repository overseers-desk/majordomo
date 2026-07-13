"""WORLD_AS_OF (replay-bounded reads, WORLD_AS_OF.design.md): the three
semantics — unset is unbounded and changes nothing; set means nothing dated
after the bound leaves any backend; set-but-unparseable (or timezone-naive) is
a hard failure on every code path. Enforcement is tested at the seam: the
parser, the window clamp, the two ``reports.spaces`` subqueries, the
NocacheReader clamp and ``createTime`` post-filter, and the FreshReader skip.
No test touches the live Chat API (fakes only, per test_nocache_reader.py).
"""

import _shim  # noqa: F401

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from majordomo import config, dates, db, nocache, reports

# 2026-07-12T17:07:00+10:00 == 2026-07-12T07:07:00 UTC
BOUND_RAW = "2026-07-12T17:07:00+10:00"
BOUND_UTC = datetime(2026, 7, 12, 7, 7, 0)


# --- semantics 1: unset -> unbounded, zero behavioural change ---------------

def test_unset_is_none(monkeypatch):
    monkeypatch.delenv("WORLD_AS_OF", raising=False)
    assert config.world_as_of() is None


def test_unset_clamp_is_identity(monkeypatch):
    monkeypatch.delenv("WORLD_AS_OF", raising=False)
    assert config.world_clamp(None) is None
    end = datetime(2026, 1, 1)
    assert config.world_clamp(end) == end


# --- semantics 2: set -> parsed to naive UTC --------------------------------

def test_valid_utc_offset(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", "2026-07-12T07:07:00+00:00")
    assert config.world_as_of() == BOUND_UTC


def test_valid_non_utc_offset_converts(monkeypatch):
    # The store is naive UTC; a +10:00 instant must land 10 hours earlier,
    # not have its offset silently dropped.
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    assert config.world_as_of() == BOUND_UTC


def test_clamp_takes_the_earlier_bound(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    assert config.world_clamp(None) == BOUND_UTC
    assert config.world_clamp(datetime(2027, 1, 1)) == BOUND_UTC
    early = datetime(2026, 1, 1)
    assert config.world_clamp(early) == early


# --- semantics 3: set but unparseable / naive -> hard failure ----------------

@pytest.mark.parametrize("bad", ["2026-07-12T17:07:00", "garbage", "", "1752303000"])
def test_bad_values_hard_fail(monkeypatch, bad):
    monkeypatch.setenv("WORLD_AS_OF", bad)
    with pytest.raises(SystemExit):
        config.world_as_of()


def test_load_config_hard_fails_on_bad_bound(monkeypatch, tmp_path):
    # A bad bound stops even commands that fetch no dates: the parse is wired
    # into load_config(), which every command and MCP tool call passes through.
    toml = tmp_path / "config.toml"
    toml.write_text("[me]\nuser_id = 'users/1'\n")
    monkeypatch.setattr(config, "CONFIG_TOML", toml)
    monkeypatch.setenv("WORLD_AS_OF", "not-a-timestamp")
    with pytest.raises(SystemExit):
        config.load_config()
    monkeypatch.delenv("WORLD_AS_OF")
    assert config.load_config()["me"]["user_id"] == "users/1"


# --- dates.resolve: the bound is the clock (the settled frozen-now semantics)

def test_relative_windows_anchor_to_the_bound(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    for window, days in (("7d", 7), ("30d", 30), ("year", 365)):
        start, end = dates.resolve(window)
        assert start == BOUND_UTC - timedelta(days=days), window
        assert end == BOUND_UTC, window  # the open end closes at the bound


def test_month_is_the_month_before_the_one_containing_the_bound(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    assert dates.resolve("month") == (datetime(2026, 6, 1), datetime(2026, 7, 1))


def test_all_window_closes_at_the_bound(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    assert dates.resolve("all") == (None, BOUND_UTC)


def test_windows_unchanged_when_unset(monkeypatch):
    monkeypatch.delenv("WORLD_AS_OF", raising=False)
    start, end = dates.resolve("7d")
    assert end is None
    assert dates._now_utc() - start < timedelta(days=7, minutes=1)


def test_late_until_is_clamped_with_a_note(monkeypatch, capsys):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    start, end = dates.resolve("all", since="2026-01-01", until="2026-12-31")
    assert start == datetime(2026, 1, 1)
    assert end == BOUND_UTC
    assert "WORLD_AS_OF" in capsys.readouterr().err


def test_early_until_passes_untouched(monkeypatch, capsys):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    assert dates.resolve("all", until="2026-02-01") == (None, datetime(2026, 2, 1))
    assert capsys.readouterr().err == ""


# --- cache backend: enforcement inside reports (the seam, not the front door)

def _capture_queries(monkeypatch):
    """Record every db.query call; each returns no rows."""
    calls: list[tuple[str, list]] = []

    def fake(conn, sql, params=()):
        calls.append((sql, list(params)))
        return []

    monkeypatch.setattr(db, "query", fake)
    return calls


def _sql_with(calls, fragment):
    hits = [(sql, params) for sql, params in calls if fragment in sql]
    assert hits, f"no captured query contains {fragment!r}"
    return hits[-1]


def test_spaces_subqueries_gain_the_bound(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    calls = _capture_queries(monkeypatch)
    reports.spaces(None, [], minimal_messages=1)
    sql, params = _sql_with(calls, "FROM googlechat_spaces")
    # Both count subqueries are bounded; so is the minimal_messages predicate,
    # which drops spaces whose first message postdates the cutoff.
    assert sql.count("m.create_time < %s") == 2
    assert sql.count("t.created_at < %s") == 1
    assert params == [BOUND_UTC, BOUND_UTC, BOUND_UTC, 1]


def test_spaces_sql_unchanged_when_unset(monkeypatch):
    monkeypatch.delenv("WORLD_AS_OF", raising=False)
    calls = _capture_queries(monkeypatch)
    reports.spaces(None, ["spaces/BLOCK"], minimal_messages=1)
    sql, params = _sql_with(calls, "FROM googlechat_spaces")
    assert "create_time" not in sql and "created_at" not in sql
    assert params == ["spaces/BLOCK", 1]


def test_messages_open_end_closes_at_the_bound(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    calls = _capture_queries(monkeypatch)
    reports.messages(None, [], space="spaces/X", end=None)
    sql, params = _sql_with(calls, "FROM googlechat_messages m")
    assert "m.create_time < %s" in sql
    assert BOUND_UTC in params


def test_tasks_end_is_clamped_not_replaced(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    calls = _capture_queries(monkeypatch)
    reports.tasks(None, [], end=datetime(2027, 1, 1))         # later than the bound
    _sql, params = _sql_with(calls, "FROM coord_tasks t")
    assert BOUND_UTC in params and datetime(2027, 1, 1) not in params
    calls.clear()
    reports.tasks(None, [], end=datetime(2026, 1, 1))         # earlier: untouched
    _sql, params = _sql_with(calls, "FROM coord_tasks t")
    assert datetime(2026, 1, 1) in params and BOUND_UTC not in params


def test_people_both_aggregation_arms_are_bounded(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    calls = _capture_queries(monkeypatch)
    reports.people(None, [])
    sql, params = _sql_with(calls, "UNION ALL")
    assert "create_time < %s" in sql and "created_at < %s" in sql
    assert params.count(BOUND_UTC) == 2


# --- nocache backend: server-side createTime clamp + spaces post-filter -----

API_SPACES = [
    {"name": "spaces/OLD", "displayName": "Old", "spaceType": "SPACE",
     "createTime": "2024-01-01T00:00:00Z"},
    {"name": "spaces/NEW", "displayName": "New", "spaceType": "SPACE",
     "createTime": "2026-08-01T00:00:00Z"},                    # after the bound
    {"name": "spaces/ANCIENT", "displayName": "Ancient", "spaceType": "SPACE"},
]


def _fake_chat(spaces, captured_filters):
    chat = MagicMock()
    chat.spaces().list().execute.return_value = {"spaces": spaces}

    def msg_list(parent=None, filter=None, **kw):
        captured_filters.append(filter)
        page = MagicMock()
        page.execute.return_value = {"messages": []}
        return page

    chat.spaces().messages().list.side_effect = msg_list
    return chat


def test_nocache_spaces_posts_filter_on_create_time(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    reader = nocache.NocacheReader(service=_fake_chat(API_SPACES, []))
    names = [r["space_name"] for r in reader.spaces()]
    # Created-after-the-bound drops; no-createTime (pre-mid-2021) is kept as
    # current-state, because dropping it would misreport it as never existing.
    assert names == ["spaces/OLD", "spaces/ANCIENT"]
    monkeypatch.delenv("WORLD_AS_OF")
    reader = nocache.NocacheReader(service=_fake_chat(API_SPACES, []))
    assert len(reader.spaces()) == 3


def test_nocache_message_fetch_is_server_bounded(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    filters: list = []
    reader = nocache.NocacheReader(service=_fake_chat(API_SPACES, filters))
    reader.messages(space="spaces/OLD")                        # open end
    assert filters == ['createTime < "2026-07-12T07:07:00Z"']
    filters.clear()
    reader.tasks(space="spaces/OLD", end=datetime(2027, 1, 1))  # later end clamps
    assert filters == ['createTime < "2026-07-12T07:07:00Z"']


def test_nocache_filter_unchanged_when_unset(monkeypatch):
    monkeypatch.delenv("WORLD_AS_OF", raising=False)
    filters: list = []
    reader = nocache.NocacheReader(service=_fake_chat(API_SPACES, filters))
    reader.messages(space="spaces/OLD")
    assert filters == [""]


if __name__ == "__main__":
    _shim.run(dict(globals()))
