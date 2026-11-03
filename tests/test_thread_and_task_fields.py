"""Message rows name their thread and say whether Google Tasks posted them, on
both paths; thread_link and dates.utc are the pure helpers beside them."""

import _shim  # noqa: F401

from datetime import datetime, timedelta, timezone

import pytest

from majordomo import api, dates, db, decoder, reports, thread_link
from majordomo.errors import MajordomoError
from test_nocache_reader import _fake_chat

TASK = "Created a task for @Ann (via Tasks)"
PLAIN = "please pay the farrier, via Tasks or not"


def test_direct_rows_carry_the_api_thread_and_the_task_event():
    msgs = {"spaces/A": [
        {"name": "spaces/A/messages/T.T", "thread": {"name": "spaces/A/threads/T"}, "text": TASK},
        {"name": "spaces/A/messages/U.V", "text": PLAIN},
    ]}
    rows = api.NocacheReader(service=_fake_chat([{"name": "spaces/A"}], msgs), blocked=[]).messages("spaces/A")
    assert [(r["thread_name"], r["task_event"]) for r in rows] == [
        ("spaces/A/threads/T", True), ("spaces/A/threads/U", False)]


def test_cache_rows_derive_the_thread_and_read_the_task_event(monkeypatch):
    monkeypatch.setattr(db, "query", lambda conn, sql, params=(): [
        {"name": "spaces/A/messages/T.T", "space_name": "spaces/A", "text": TASK},
        {"name": "spaces/A/messages/U.V", "space_name": "spaces/A", "text": PLAIN}])
    monkeypatch.setattr(reports, "_floor_check", lambda *a, **kw: None)
    rows = reports.messages(None, [], space="spaces/A")
    assert [(r["thread_name"], r["task_event"]) for r in rows] == [
        ("spaces/A/threads/T", True), ("spaces/A/threads/U", False)]


@pytest.mark.parametrize("text, event", [
    (TASK, True), ("Completed a task (via Tasks)", True), ("Assigned a task to @Bo (via Tasks)", True),
    (PLAIN, False), ("Created a task for @Ann", False), (None, False)])
def test_a_task_event_is_exactly_what_the_decoder_reads(text, event):
    assert decoder.is_task_event({"text": text}) is event


def test_thread_link():
    assert thread_link("spaces/AAA/threads/b-1_c") == "https://chat.google.com/room/AAA/b-1_c"
    assert thread_link("spaces/AAA/messages/b") is None
    assert thread_link(None) is None


def test_utc_reads_naive_aware_and_text():
    want = datetime(2026, 11, 3, 1, 2, 3, tzinfo=timezone.utc)
    assert dates.utc(datetime(2026, 11, 3, 1, 2, 3)) == want
    assert dates.utc(datetime(2026, 11, 3, 11, 2, 3, tzinfo=timezone(timedelta(hours=10)))).tzinfo == timezone.utc
    assert dates.utc("2026-11-03T01:02:03Z") == want
    assert dates.utc("2026-11-03T01:02:03") == want
    assert dates.utc(None) is None and dates.utc("") is None
    with pytest.raises(MajordomoError):
        dates.utc("yesterday")
