"""Live reader behaviour, with an injected fake Chat service (no creds/network),
and the cache->live fallback in make_reader.
"""

import _shim  # noqa: F401

from unittest.mock import MagicMock

from majordomo import db, live, readers


def _fake_chat(spaces: list, msgs: dict):
    """Mimic the google-api chained builder: chat.spaces().list().execute() and
    chat.spaces().messages().list(parent=...).execute()."""
    chat = MagicMock()
    chat.spaces().list().execute.return_value = {"spaces": spaces}

    def msg_list(parent=None, **kw):
        page = MagicMock()
        page.execute.return_value = {"messages": msgs.get(parent, [])}
        return page

    chat.spaces().messages().list.side_effect = msg_list
    return chat


SPACES = [
    {"name": "spaces/OK", "displayName": "Work", "spaceType": "SPACE"},
    {"name": "spaces/BLOCK", "displayName": "Private", "spaceType": "SPACE"},
]
MSGS = {
    "spaces/OK": [
        {"name": "spaces/OK/messages/T.1", "createTime": "2026-06-01T08:00:00Z", "text": "ship the thing",
         "sender": {"name": "users/9", "type": "HUMAN"}},
        {"name": "spaces/OK/messages/T.2", "createTime": "2026-06-01T08:05:00Z",
         "text": "Created a task for @Alice (via Tasks)", "sender": {"name": "users/9", "type": "HUMAN"},
         "annotations": [{"type": "USER_MENTION", "userMention": {"user": {"name": "users/1"}}}]},
    ],
    "spaces/BLOCK": [
        {"name": "spaces/BLOCK/messages/B.2", "createTime": "2026-06-01T09:00:00Z",
         "text": "Created a task for @Bob (via Tasks)", "sender": {"name": "users/7"},
         "annotations": [{"type": "USER_MENTION", "userMention": {"user": {"name": "users/2"}}}]},
    ],
}


def _reader(blocked):
    return live.LiveReader(service=_fake_chat(SPACES, MSGS), blocked=blocked)


def test_decodes_and_recovers_title():
    rows = _reader([]).tasks()
    ok = [r for r in rows if r["space_name"] == "spaces/OK"]
    assert len(ok) == 1
    assert ok[0]["assignee_user_name"] == "users/1"
    assert ok[0]["assignee"] == "Alice"
    assert ok[0]["title"] == "ship the thing"        # recovered from the prior plain message
    assert ok[0]["status"] == "open"


def test_sieve_drops_blocked_space():
    rows = _reader(["spaces/BLOCK"]).tasks()
    assert all(r["space_name"] != "spaces/BLOCK" for r in rows)
    assert any(r["space_name"] == "spaces/OK" for r in rows)


def test_to_me_and_by_me_filters():
    r = _reader(["spaces/BLOCK"])                     # isolate to the OK space
    assert len(r.tasks(to_user="users/1")) == 1      # assignee
    assert len(r.tasks(to_user="users/2")) == 0
    assert len(r.tasks(by_user="users/9")) == 1      # sender of the via-Tasks message
    assert len(r.tasks(by_user="users/8")) == 0


def test_make_reader_auto_falls_back_to_live_when_db_down():
    orig_connect, orig_from = db.connect, live.LiveReader.from_config
    db.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
    live.LiveReader.from_config = staticmethod(lambda cfg, blocked: "LIVE")
    try:
        assert readers.make_reader({"sieve": {}}, None) == "LIVE"      # auto -> live
        forced_cache_raised = False
        try:
            readers.make_reader({"sieve": {}}, "cache")                # forced cache -> loud
        except RuntimeError:
            forced_cache_raised = True
        assert forced_cache_raised
    finally:
        db.connect, live.LiveReader.from_config = orig_connect, orig_from


if __name__ == "__main__":
    _shim.run(dict(globals()))
