"""WORLD_AS_OF (replay-bounded reads, WORLD_AS_OF.design.md): the three
semantics: unset is unbounded and changes nothing; set means nothing dated
after the bound leaves any backend; set-but-unparseable (or timezone-naive) is
a hard failure on every code path. The bound reaches the core only through
the config (``load_config(..., world_as_of=)``); the core never reads the
environment, which the front doors read and hand in. Enforcement is tested at
the seam: the parser, the window clamp, the two ``reports.spaces``
subqueries, the NocacheReader clamp and ``createTime`` post-filter, and the
FreshReader skip. What the core has to say about a bound (a clamped
``--until``, a store that does not reach it, a skipped top-up) is a note for
a front door to show; the core itself prints nothing. No test touches the
live Chat API (fakes only, per test_nocache_reader.py).
"""

import _shim  # noqa: F401

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from majordomo import api, config, dates, db, frontdoor, output, readers, reports
from majordomo.errors import MajordomoError

# 2026-07-12T17:07:00+10:00 == 2026-07-12T07:07:00 UTC
BOUND_RAW = "2026-07-12T17:07:00+10:00"
BOUND_UTC = datetime(2026, 7, 12, 7, 7, 0)
BOUNDED = config.load_config({}, world_as_of=BOUND_RAW)


@pytest.fixture(autouse=True)
def _no_notes_carried_in():
    config.drain_notes()


# --- semantics 1: unset -> unbounded, zero behavioural change ---------------

def test_unset_is_none():
    assert config.world_as_of(config.load_config({})) is None


def test_unset_clamp_is_identity():
    assert config.world_clamp(None, None) is None
    end = datetime(2026, 1, 1)
    assert config.world_clamp(end, None) == end


def test_the_environment_never_bounds_a_config_handed_none(monkeypatch):
    # A library caller is bounded by what it hands load_config, whatever its
    # host exports: a set (even a bad) WORLD_AS_OF reaches nothing.
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    cfg = config.load_config({})
    assert "_world_as_of" not in cfg and config.world_as_of(cfg) is None
    assert dates.resolve("all", cfg=cfg) == (None, None)
    assert readers.CacheReader(None, [], cfg=cfg).bound is None
    monkeypatch.setenv("WORLD_AS_OF", "garbage")
    assert config.load_config({"sieve": {}}) == {"sieve": {}}


# --- semantics 2: set -> parsed to naive UTC --------------------------------

def test_valid_utc_offset():
    assert config.world_as_of(config.load_config({}, world_as_of="2026-07-12T07:07:00+00:00")) == BOUND_UTC


def test_valid_non_utc_offset_converts():
    # The store is naive UTC; a +10:00 instant must land 10 hours earlier,
    # not have its offset silently dropped.
    assert config.world_as_of(BOUNDED) == BOUND_UTC


def test_the_config_keeps_the_bound_as_it_was_given():
    assert BOUNDED["_world_as_of"] == BOUND_RAW
    aware = datetime(2026, 7, 12, 7, 7, tzinfo=timezone.utc)
    cfg = config.load_config({}, world_as_of=aware)
    assert cfg["_world_as_of"] == aware.isoformat()
    assert config.world_as_of(cfg) == BOUND_UTC


def test_clamp_takes_the_earlier_bound():
    assert config.world_clamp(None, BOUND_UTC) == BOUND_UTC
    assert config.world_clamp(datetime(2027, 1, 1), BOUND_UTC) == BOUND_UTC
    early = datetime(2026, 1, 1)
    assert config.world_clamp(early, BOUND_UTC) == early


# --- semantics 3: set but unparseable / naive -> hard failure ----------------

@pytest.mark.parametrize("bad", ["2026-07-12T17:07:00", "garbage", "", "1752303000",
                                 datetime(2026, 7, 12, 17, 7)])
def test_bad_values_hard_fail(bad):
    with pytest.raises(MajordomoError, match="WORLD_AS_OF="):
        config.load_config({}, world_as_of=bad)


def test_a_bad_bound_stops_even_a_missing_config(tmp_path):
    # The bound is parsed before anything else, so a bad one is the refusal a
    # command meets, whatever else is wrong with it.
    with pytest.raises(MajordomoError, match="WORLD_AS_OF='garbage'"):
        config.load_config(tmp_path / "absent.toml", world_as_of="garbage")


def test_a_bad_bound_in_a_config_fails_where_it_is_read():
    with pytest.raises(MajordomoError, match="WORLD_AS_OF='garbage'"):
        config.world_as_of({"_world_as_of": "garbage"})


def test_the_front_doors_hard_fail_on_a_bad_bound_in_the_environment(monkeypatch, tmp_path):
    # A bad bound stops even commands that fetch no dates: frontdoor.load,
    # which every command and MCP tool call passes through, hands the
    # environment's WORLD_AS_OF to load_config.
    (tmp_path / "config.toml").write_text("[me]\nuser_id = 'users/1'\n")
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("WORLD_AS_OF", "not-a-timestamp")
    with pytest.raises(MajordomoError):
        frontdoor.load()
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    assert config.world_as_of(frontdoor.load()) == BOUND_UTC
    monkeypatch.delenv("WORLD_AS_OF")
    cfg = frontdoor.load()
    assert cfg["me"]["user_id"] == "users/1" and config.world_as_of(cfg) is None


# --- dates.resolve: the bound is the clock (the settled frozen-now semantics)

def test_relative_windows_anchor_to_the_bound():
    for window, days in (("7d", 7), ("30d", 30), ("year", 365)):
        start, end = dates.resolve(window, cfg=BOUNDED)
        assert start == BOUND_UTC - timedelta(days=days), window
        assert end == BOUND_UTC, window  # the open end closes at the bound


def test_month_is_the_month_before_the_one_containing_the_bound():
    assert dates.resolve("month", cfg=BOUNDED) == (datetime(2026, 6, 1), datetime(2026, 7, 1))


def test_all_window_closes_at_the_bound():
    assert dates.resolve("all", cfg=BOUNDED) == (None, BOUND_UTC)


def test_windows_unchanged_when_unset():
    start, end = dates.resolve("7d", cfg=config.load_config({}))
    assert end is None
    assert dates._now_utc() - start < timedelta(days=7, minutes=1)


def test_late_until_is_clamped_with_a_note(capsys):
    start, end = dates.resolve("all", since="2026-01-01", until="2026-12-31", cfg=BOUNDED)
    assert start == datetime(2026, 1, 1)
    assert end == BOUND_UTC
    assert capsys.readouterr().err == ""
    assert config.drain_notes() == [
        "majordomo: --until '2026-12-31' is after WORLD_AS_OF (2026-07-12T07:07:00 UTC); clamped to the bound."
    ]


def test_early_until_passes_untouched():
    assert dates.resolve("all", until="2026-02-01", cfg=BOUNDED) == (None, datetime(2026, 2, 1))
    assert config.drain_notes() == []


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
    calls = _capture_queries(monkeypatch)
    reports.spaces(None, [], minimal_messages=1, bound=BOUND_UTC)
    sql, params = _sql_with(calls, "FROM googlechat_spaces")
    # Both count subqueries are bounded; so is the minimal_messages predicate,
    # which drops spaces whose first message postdates the cutoff.
    assert sql.count("m.create_time < %s") == 2
    assert sql.count("t.created_at < %s") == 1
    assert params == [BOUND_UTC, BOUND_UTC, BOUND_UTC, 1]


def test_spaces_sql_unchanged_when_unset(monkeypatch):
    calls = _capture_queries(monkeypatch)
    reports.spaces(None, ["spaces/BLOCK"], minimal_messages=1)
    sql, params = _sql_with(calls, "FROM googlechat_spaces")
    assert "create_time" not in sql and "created_at" not in sql
    assert params == ["spaces/BLOCK", 1]


def test_messages_open_end_closes_at_the_bound(monkeypatch):
    calls = _capture_queries(monkeypatch)
    reports.messages(None, [], space="spaces/X", end=None, bound=BOUND_UTC)
    sql, params = _sql_with(calls, "FROM googlechat_messages m")
    assert "m.create_time < %s" in sql
    assert BOUND_UTC in params


def test_tasks_end_is_clamped_not_replaced(monkeypatch):
    calls = _capture_queries(monkeypatch)
    reports.tasks(None, [], end=datetime(2027, 1, 1), bound=BOUND_UTC)         # later than the bound
    _sql, params = _sql_with(calls, "FROM coord_tasks t")
    assert BOUND_UTC in params and datetime(2027, 1, 1) not in params
    calls.clear()
    reports.tasks(None, [], end=datetime(2026, 1, 1), bound=BOUND_UTC)         # earlier: untouched
    _sql, params = _sql_with(calls, "FROM coord_tasks t")
    assert datetime(2026, 1, 1) in params and BOUND_UTC not in params


def test_people_both_aggregation_arms_are_bounded(monkeypatch):
    calls = _capture_queries(monkeypatch)
    reports.people(None, [], bound=BOUND_UTC)
    sql, params = _sql_with(calls, "UNION ALL")
    assert "create_time < %s" in sql and "created_at < %s" in sql
    assert params.count(BOUND_UTC) == 2


def test_the_cache_reader_takes_its_bound_from_the_config(monkeypatch):
    calls = _capture_queries(monkeypatch)
    reader = readers.CacheReader(None, [], cfg=BOUNDED)
    assert reader.roster.frozen
    reader.tasks(end=datetime(2027, 1, 1))
    _sql, params = _sql_with(calls, "FROM coord_tasks t")
    assert BOUND_UTC in params and datetime(2027, 1, 1) not in params
    assert not readers.CacheReader(None, [], cfg={}).roster.frozen


def test_a_nocache_reader_from_a_config_carries_its_bound(monkeypatch):
    monkeypatch.setattr(api, "_require_google", lambda: (None, None, lambda *a, **k: MagicMock()))
    reader = api.NocacheReader.from_config(BOUNDED, [], creds=object())
    assert reader.bound == BOUND_UTC and reader.roster.frozen
    assert api.NocacheReader.from_config({}, [], creds=object()).bound is None


# --- FreshReader (--live): degrade to cache when the mirror reaches the bound

def _fresh(cache, nc, cfg=BOUNDED):
    fr = readers.FreshReader(cache, cfg, [], [])
    fr._nc = nc  # skip the real NocacheReader build
    return fr


def test_live_topup_skipped_when_watermark_reaches_the_bound(monkeypatch, capsys):
    cache = MagicMock()
    cache.tasks.return_value = ["CACHE_ROW"]
    nc = MagicMock()
    monkeypatch.setattr(readers.reports, "space_watermark",
                        lambda conn, sp: datetime(2026, 7, 13))  # past the bound
    out = _fresh(cache, nc).tasks(space="spaces/X")
    assert out == ["CACHE_ROW"]                                  # cache served as-is
    nc.tasks.assert_not_called()                                 # no API call at all
    assert capsys.readouterr().err == ""                         # it says so in a note
    notes = config.drain_notes()
    assert len(notes) == 1 and "--live top-up skipped" in notes[0]


def test_live_topup_survives_a_watermark_short_of_the_bound(monkeypatch):
    # The legitimate case (a bound in the future or inside the sync gap): the
    # top-up runs; its fetch is end-clamped inside NocacheReader.
    cache = MagicMock()
    cache.messages.return_value = []
    nc = MagicMock()
    nc.messages.return_value = []
    monkeypatch.setattr(readers.reports, "space_watermark",
                        lambda conn, sp: datetime(2026, 7, 1))   # short of the bound
    _fresh(cache, nc).messages("spaces/X")
    nc.messages.assert_called_once()
    assert config.drain_notes() == []


def test_live_unbounded_polls_as_before(monkeypatch):
    cache = MagicMock()
    cache.tasks.return_value = []
    nc = MagicMock()
    nc.tasks.return_value = []
    monkeypatch.setattr(readers.reports, "space_watermark",
                        lambda conn, sp: datetime(2026, 7, 13))
    _fresh(cache, nc, cfg={}).tasks(space="spaces/X")
    nc.tasks.assert_called_once()


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


def test_nocache_spaces_posts_filter_on_create_time():
    reader = api.NocacheReader(service=_fake_chat(API_SPACES, []), bound=BOUND_UTC)
    names = [r["space_name"] for r in reader.spaces()]
    # Created-after-the-bound drops; no-createTime (pre-mid-2021) is kept as
    # current-state, because dropping it would misreport it as never existing.
    assert names == ["spaces/OLD", "spaces/ANCIENT"]
    reader = api.NocacheReader(service=_fake_chat(API_SPACES, []))
    assert len(reader.spaces()) == 3


def test_nocache_message_fetch_is_server_bounded():
    filters: list = []
    reader = api.NocacheReader(service=_fake_chat(API_SPACES, filters), bound=BOUND_UTC)
    reader.messages(space="spaces/OLD")                        # open end
    assert filters == ['createTime < "2026-07-12T07:07:00Z"']
    filters.clear()
    reader.tasks(space="spaces/OLD", end=datetime(2027, 1, 1))  # later end clamps
    assert filters == ['createTime < "2026-07-12T07:07:00Z"']


def test_nocache_filter_unchanged_when_unset():
    filters: list = []
    reader = api.NocacheReader(service=_fake_chat(API_SPACES, filters))
    reader.messages(space="spaces/OLD")
    assert filters == [""]


# --- flagging: the envelope proves the bound; honesty warnings ---------------

def test_json_envelope_carries_the_bound(capsys):
    import json
    output.emit([{"a": 1}], [("A", "a")], "cache", output_json=True, world_as_of=BOUND_RAW)
    env = json.loads(capsys.readouterr().out)
    assert env["world_as_of"] == BOUND_RAW
    assert env["current_state_note"] == config.WORLD_CURRENT_STATE_NOTE


def test_json_envelope_unchanged_when_unset(monkeypatch, capsys):
    import json
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)  # the environment is not the bound
    output.emit([{"a": 1}], [("A", "a")], "cache", output_json=True)
    env = json.loads(capsys.readouterr().out)
    assert "world_as_of" not in env and "current_state_note" not in env


@pytest.mark.parametrize("args", [["spaces"], ["spaces", "--owner"], ["people"], ["tasks"],
                                  ["messages", "--space", "spaces/A"], ["attachments", "--space", "spaces/A"]])
def test_the_cli_bounds_every_report_by_the_environment_s_bound(args, monkeypatch, tmp_path):
    import json
    import types

    from typer.testing import CliRunner

    from majordomo.cli import app

    (tmp_path / "config.toml").write_text("[me]\nuser_id = 'users/1'\n")
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude-here"))
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    monkeypatch.setattr(api, "ensure_command_scopes", lambda cfg, **flags: None)
    ends = []

    def report(*a, end=None, **kw):
        ends.append(end)
        return []

    reader = types.SimpleNamespace(source="cache", spaces=report, people=report, tasks=report, messages=report)
    monkeypatch.setattr(readers, "make_reader", lambda cfg, source: reader)
    monkeypatch.setattr(api, "owned_spaces", lambda cfg, blocked, **kw: [])
    monkeypatch.setattr(api, "attachments", lambda cfg, blocked, **kw: report(**kw))
    result = CliRunner().invoke(app, args + ["--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["world_as_of"] == BOUND_RAW
    if args[0] != "spaces":  # every window closes at or before the bound
        assert ends and all(end is not None and end <= BOUND_UTC for end in ends)


def test_console_flags_the_bound_on_stderr(capsys):
    output.emit([{"a": 1}], [("A", "a")], "cache", output_csv=True, world_as_of=BOUND_RAW)
    captured = capsys.readouterr()
    assert BOUND_RAW in captured.err
    assert "WORLD_AS_OF" not in captured.out  # rows themselves stay clean


def test_cache_floor_warning(monkeypatch, capsys):
    # A bound older than the oldest cached message is contamination by
    # omission: the store cannot reach the as-of instant, and says so.
    def fake(conn, sql, params=()):
        if "MIN(create_time)" in sql:
            return [{"floor": datetime(2026, 7, 20)}]  # oldest row postdates bound
        return []

    monkeypatch.setattr(db, "query", fake)
    reports.messages(None, [], space="spaces/X", bound=BOUND_UTC)
    assert capsys.readouterr().err == ""
    notes = config.drain_notes()
    assert len(notes) == 1 and "does not reach the as-of instant" in notes[0]


def test_no_floor_warning_when_store_reaches_the_bound(monkeypatch):
    def fake(conn, sql, params=()):
        if "MIN(create_time)" in sql:
            return [{"floor": datetime(2025, 7, 1)}]
        return []

    monkeypatch.setattr(db, "query", fake)
    reports.messages(None, [], space="spaces/X", bound=BOUND_UTC)
    assert config.drain_notes() == []


def test_edited_after_bound_is_marked_not_dropped():
    chat = MagicMock()
    chat.spaces().list().execute.return_value = {"spaces": API_SPACES[:1]}
    page = MagicMock()
    page.execute.return_value = {"messages": [
        {"name": "spaces/OLD/messages/A.1", "createTime": "2026-07-01T00:00:00Z",
         "lastUpdateTime": "2026-07-13T00:00:00Z", "text": "edited later"},
        {"name": "spaces/OLD/messages/A.2", "createTime": "2026-07-01T01:00:00Z",
         "text": "untouched"},
    ]}
    chat.spaces().messages().list.return_value = page
    rows = api.NocacheReader(service=chat, bound=BOUND_UTC).messages(space="spaces/OLD")
    by = {r["name"]: r for r in rows}
    assert by["spaces/OLD/messages/A.1"]["edited_after_bound"] is True
    assert "edited_after_bound" not in by["spaces/OLD/messages/A.2"]


if __name__ == "__main__":
    _shim.run(dict(globals()))
