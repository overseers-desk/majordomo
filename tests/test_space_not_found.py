"""A space named by id that is blocked answers exactly as one that does not
exist, on every read source: the same class and the same words, so a caller
cannot probe the block list. A thread is judged by its space; a thread missing
from a space that exists is no rows, not an error. `--live` reads a space the
mirror lacks over the API."""

import _shim  # noqa: F401

import types
from unittest.mock import MagicMock

import pytest

from majordomo import api, readers, reports, roster
from majordomo.errors import NotFound

MSGS = {
    "spaces/A": [
        {"name": "spaces/A/messages/T.1", "createTime": "2026-06-01T08:00:00Z", "text": "hello",
         "sender": {"name": "users/9", "type": "HUMAN"}}],
    "spaces/S": [
        {"name": "spaces/S/messages/P.1", "createTime": "2026-06-01T09:00:00Z", "text": "private",
         "sender": {"name": "users/7", "type": "HUMAN"}}],
}


def _refusal(call):
    with pytest.raises(NotFound) as ei:
        call()
    return type(ei.value), str(ei.value)


# --- the cache ---------------------------------------------------------------

@pytest.fixture
def mirror(monkeypatch):
    """A mirror holding spaces/A, and spaces/S unless told otherwise."""
    held = {"spaces/A", "spaces/S"}
    monkeypatch.setattr(reports, "space_known", lambda conn, s: s in held)

    def messages(conn, blocked, *, space=None, thread=None, **kw):
        rows = [dict(m, space_name=sp) for sp, ms in MSGS.items() for m in ms]
        return [r for r in rows if (not space or r["space_name"] == space)
                and (not thread or r["name"].startswith(thread.split(".")[0] + "."))]

    monkeypatch.setattr(reports, "messages", messages)
    monkeypatch.setattr(reports, "tasks", lambda conn, blocked, **kw: [])
    return held


def _cache(tmp_path, blocked):
    return readers.CacheReader(None, blocked, ros=roster.Roster(tmp_path))


def test_cache_answers_a_blocked_id_as_an_absent_one(mirror, tmp_path):
    reader = _cache(tmp_path, ["spaces/S"])
    blocked = [_refusal(lambda: reader.messages("spaces/S")), _refusal(lambda: reader.tasks(space="spaces/S"))]
    mirror.discard("spaces/S")
    reader = _cache(tmp_path, [])
    absent = [_refusal(lambda: reader.messages("spaces/S")), _refusal(lambda: reader.tasks(space="spaces/S"))]
    assert blocked == absent == [(
        NotFound, "majordomo: spaces/S: not found in the cache (--live or --nocache asks the Chat API).")] * 2


def test_cache_judges_a_thread_by_its_space(mirror, tmp_path):
    thread = "spaces/S/messages/P"
    blocked = _refusal(lambda: _cache(tmp_path, ["spaces/S"]).messages(thread=thread))
    mirror.discard("spaces/S")
    assert blocked == _refusal(lambda: _cache(tmp_path, []).messages(thread=thread))
    assert _cache(tmp_path, []).messages(thread="spaces/A/messages/NONE") == []
    assert [r["name"] for r in _cache(tmp_path, []).messages(thread="spaces/A/messages/T")] == [
        "spaces/A/messages/T.1"]


# --- the Chat API ------------------------------------------------------------

def _chat(spaces: dict):
    """A Chat service holding ``spaces``; any other parent answers 404."""
    chat = MagicMock()
    chat.spaces().list().execute.return_value = {"spaces": [{"name": s} for s in spaces]}

    def msg_list(parent=None, **kw):
        page = MagicMock()
        if parent in spaces:
            page.execute.return_value = {"messages": spaces[parent]}
        else:
            err = Exception("404")
            err.resp = types.SimpleNamespace(status=404)
            page.execute.side_effect = err
        return page

    chat.spaces().messages().list.side_effect = msg_list
    return chat


def _nocache(tmp_path, spaces, blocked):
    return api.NocacheReader(service=_chat(spaces), blocked=blocked, ros=roster.Roster(tmp_path))


def test_nocache_answers_a_blocked_id_as_an_absent_one(tmp_path):
    def refusals(reader):
        return [_refusal(lambda: reader.messages(space="spaces/S")), _refusal(lambda: reader.tasks(space="spaces/S"))]

    blocked = refusals(_nocache(tmp_path, MSGS, ["spaces/S"]))
    absent = refusals(_nocache(tmp_path, {"spaces/A": MSGS["spaces/A"]}, []))
    assert blocked == absent == [(NotFound, "majordomo: spaces/S: not found.")] * 2


def test_nocache_judges_a_thread_by_its_space(tmp_path):
    thread = "spaces/S/messages/P.1"
    blocked = _refusal(lambda: _nocache(tmp_path, MSGS, ["spaces/S"]).messages(thread=thread))
    absent = _refusal(lambda: _nocache(tmp_path, {"spaces/A": MSGS["spaces/A"]}, []).messages(thread=thread))
    assert blocked == absent
    assert _nocache(tmp_path, MSGS, []).messages(thread="spaces/A/messages/NONE") == []


def test_other_api_errors_stay_loud(tmp_path):
    chat = _chat({})
    page = MagicMock()
    err = Exception("boom")
    err.resp = types.SimpleNamespace(status=500)
    page.execute.side_effect = err
    chat.spaces().messages().list.side_effect = lambda **kw: page
    reader = api.NocacheReader(service=chat, blocked=[], ros=roster.Roster(tmp_path))
    with pytest.raises(Exception, match="boom") as ei:
        reader.messages(space="spaces/A")
    assert not isinstance(ei.value, NotFound)


# --- --live ------------------------------------------------------------------

def _live(tmp_path, spaces, blocked):
    fresh = readers.FreshReader(_cache(tmp_path, blocked), {}, blocked, [])
    fresh._nc = _nocache(tmp_path, spaces, blocked)
    return fresh


def test_live_reads_a_space_the_mirror_lacks_over_the_api(mirror, tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "space_watermark", lambda conn, sp: None)
    mirror.discard("spaces/S")
    fresh = _live(tmp_path, MSGS, [])
    assert [r["name"] for r in fresh.messages("spaces/S")] == ["spaces/S/messages/P.1"]
    assert [r["name"] for r in fresh.messages(thread="spaces/S/messages/P")] == ["spaces/S/messages/P.1"]
    assert fresh.tasks(space="spaces/S") == []


def test_live_answers_a_blocked_id_as_one_absent_everywhere(mirror, tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "space_watermark", lambda conn, sp: None)

    def refusals(fresh):
        return [_refusal(lambda: fresh.messages("spaces/S")),
                _refusal(lambda: fresh.messages(thread="spaces/S/messages/P")),
                _refusal(lambda: fresh.tasks(space="spaces/S"))]

    blocked = refusals(_live(tmp_path, MSGS, ["spaces/S"]))
    mirror.discard("spaces/S")
    absent = refusals(_live(tmp_path, {"spaces/A": MSGS["spaces/A"]}, []))
    assert blocked == absent == [(NotFound, "majordomo: spaces/S: not found.")] * 3


if __name__ == "__main__":
    _shim.run(dict(globals()))
