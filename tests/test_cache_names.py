"""The cache path names people through the same roster as the direct API:
task assignees (the People name over the mirror's), message senders, the
people report (People name first, the mirror's frozen spellings after), and
name matching against the mirror's spellings when the cache file has none."""

import _shim  # noqa: F401

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from majordomo import readers, reports, roster


def _people(directory):
    svc = MagicMock()

    def batch_get(resourceNames=None, **kw):
        req = MagicMock()
        req.execute.return_value = {"responses": [
            {"requestedResourceName": n, "person": {"resourceName": n, "names": [
                {"displayName": directory[n], "metadata": {"source": {"type": "PROFILE"}}}]}}
            for n in resourceNames if n in directory]}
        return req

    svc.people().getBatchGet.side_effect = batch_get
    return lambda: svc


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "tasks", lambda conn, blocked, **kw: [
        {"source_message_name": "spaces/A/messages/1", "space_name": "spaces/A",
         "assignee_user_name": "users/1", "assignee": None, "created_at": datetime(2026, 5, 1)},
        {"source_message_name": "spaces/A/messages/2", "space_name": "spaces/A",
         "assignee_user_name": "users/2", "assignee": "Rex", "created_at": datetime(2026, 5, 2)}])
    monkeypatch.setattr(reports, "messages", lambda conn, blocked, **kw: [
        {"name": "spaces/A/messages/1", "space_name": "spaces/A", "sender_name": "users/1"}])
    monkeypatch.setattr(reports, "people", lambda conn, blocked, **kw: [
        {"user_id": "users/1", "display": "Ada Q", "msgs": 3, "tasks": 1}])
    monkeypatch.setattr(reports, "mention_rows", lambda conn, blocked: [])
    monkeypatch.setattr(reports, "task_names", lambda conn, blocked: [
        {"user_id": "users/2", "display": "Rex Tanaka", "last_seen": datetime(2026, 5, 2)}])
    monkeypatch.setattr(reports, "dm_spaces", lambda conn, blocked, user=None: [
        {"space_name": "spaces/DM1", "sender_name": "users/1", "sender_type": "HUMAN", "msgs": 2}])
    ros = roster.Roster(tmp_path, lookup=_people({"people/1": "Ada Quill"}))
    return readers.CacheReader(None, [], cfg={"me": {"user_id": "users/me"}}, ros=ros)


def test_tasks_name_the_assignee_and_keep_the_mirror_name_as_fallback(cache):
    assert [r["assignee"] for r in cache.tasks()] == ["Ada Quill", "Rex"]


def test_messages_name_the_sender(cache):
    assert cache.messages("spaces/A")[0]["sender_display"] == "Ada Quill"


def test_people_puts_the_people_name_first(cache):
    row = cache.people()[0]
    assert row["display"] == "Ada Quill" and row["names"] == ["Ada Quill", "Ada Q"]
    assert row["dm_space"] == "spaces/DM1"


def test_a_name_only_the_mirror_holds_still_resolves(cache):
    assert cache.resolve_person("rex tanaka") == "users/2"


def test_block_assignees_by_either_name(cache):
    cache.blocked_assignees = ["Ada Quill"]
    assert [r["assignee_user_name"] for r in cache.tasks()] == ["users/2"]
    cache.blocked_assignees = ["Rex"]
    assert [r["assignee_user_name"] for r in cache.tasks()] == ["users/1"]


if __name__ == "__main__":
    _shim.run(dict(globals()))
