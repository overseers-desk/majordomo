"""NocacheReader (direct Chat API) behaviour, with an injected fake Chat service
(no creds/network), and the cache->nocache fallback in make_reader.
"""

import _shim  # noqa: F401

from unittest.mock import MagicMock

from majordomo import api, db, readers


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
    return api.NocacheReader(service=_fake_chat(SPACES, MSGS), blocked=blocked)


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


def test_people_broadened_counts_senders_and_assignees():
    by = {r["user_id"]: r for r in _reader(["spaces/BLOCK"]).people()}
    assert by["users/9"]["msgs"] == 2 and by["users/9"]["tasks"] == 0   # sender of both OK msgs
    assert by["users/1"]["tasks"] == 1 and by["users/1"]["display"] == "Alice"


def test_assignee_name_glob_nocache():
    r = _reader(["spaces/BLOCK"])
    assert len(r.tasks(assignee_name="*Ali*")) == 1
    assert len(r.tasks(assignee_name="*Zzz*")) == 0


def test_block_assignees_nocache():
    r = api.NocacheReader(service=_fake_chat(SPACES, MSGS), blocked=["spaces/BLOCK"], blocked_assignees=["users/1"])
    assert r.tasks() == []                                              # Alice (users/1) was the only OK task


def test_make_reader_auto_falls_back_to_nocache_when_db_down():
    orig_connect, orig_from = db.connect, api.NocacheReader.from_config
    db.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
    api.NocacheReader.from_config = staticmethod(lambda cfg, blocked, blocked_assignees=None: "NOCACHE")
    try:
        assert readers.make_reader({"sieve": {}}, None) == "NOCACHE"      # auto -> nocache
        message = None
        try:
            readers.make_reader({"sieve": {}}, "cache")                # forced cache -> loud, one line
        except SystemExit as exc:
            message = str(exc)
        assert message is not None, "forced cache with a dead DB did not fail"
        assert "cache unreachable" in message and "db down" in message  # clean answer, cause named
    finally:
        db.connect, api.NocacheReader.from_config = orig_connect, orig_from



# --- people and spaces by name, through known.py ---------------------------

def _chat_with_dm(user_to_space):
    import types
    chat = _fake_chat(SPACES, MSGS)

    def find_dm(name=None):
        page = MagicMock()
        if name in user_to_space:
            page.execute.return_value = {"name": user_to_space[name], "spaceType": "DIRECT_MESSAGE"}
        else:
            err = Exception("404")
            err.resp = types.SimpleNamespace(status=404)
            page.execute.side_effect = err
        return page

    chat.spaces().findDirectMessage.side_effect = find_dm
    return chat


def test_a_read_learns_names_and_a_name_then_resolves(tmp_path):
    from majordomo import known
    kn = known.Known(tmp_path / "known.tsv")
    r = api.NocacheReader(service=_fake_chat(SPACES, MSGS), blocked=[], kn=kn)
    r.tasks()
    assert kn.names_of("users/1") == ["Alice"]
    assert r.resolve_person("alice") == "users/1"
    assert (tmp_path / "known.tsv").exists()


def test_space_by_display_name(tmp_path):
    from majordomo import known
    kn = known.Known(tmp_path / "known.tsv")
    r = api.NocacheReader(service=_fake_chat(SPACES, MSGS), blocked=["spaces/BLOCK"], kn=kn)
    assert r.resolve_space("work") == "spaces/OK"
    try:
        r.resolve_space("Private")  # blocked spaces are never listed, so never named
    except SystemExit as exc:
        assert "no space named" in str(exc)
    else:
        raise AssertionError("a blocked space must not resolve by name")


def test_person_alone_reads_the_dm(tmp_path):
    from majordomo import known
    kn = known.Known(tmp_path / "known.tsv")
    msgs = dict(MSGS, **{"spaces/DM9": [
        {"name": "spaces/DM9/messages/D.1", "createTime": "2026-06-02T08:00:00Z", "text": "hi",
         "sender": {"name": "users/9", "type": "HUMAN"}}]})
    chat = _chat_with_dm({"users/9": "spaces/DM9"})
    chat.spaces().messages().list.side_effect = lambda parent=None, **kw: _page(msgs.get(parent, []))
    r = api.NocacheReader(service=chat, blocked=[], kn=kn, me="users/me")
    rows = r.messages(person="users/9")
    assert [x["name"] for x in rows] == ["spaces/DM9/messages/D.1"]
    assert kn.dm_space_of("users/9") == "spaces/DM9"
    assert r.messages(space="spaces/OK", person="users/9") and all(
        x["sender_name"] == "users/9" for x in r.messages(space="spaces/OK", person="users/9"))


def test_email_resolves_through_the_dm(tmp_path):
    from majordomo import known
    kn = known.Known(tmp_path / "known.tsv")
    msgs = {"spaces/DM9": [
        {"name": "spaces/DM9/messages/D.1", "createTime": "2026-06-02T08:00:00Z", "text": "hi",
         "sender": {"name": "users/9", "type": "HUMAN"}}]}
    chat = _chat_with_dm({"users/nine@example.com": "spaces/DM9"})
    chat.spaces().messages().list.side_effect = lambda parent=None, **kw: _page(msgs.get(parent, []))
    r = api.NocacheReader(service=chat, blocked=[], kn=kn, me="users/me")
    assert r.resolve_person("nine@example.com") == "users/9"
    assert kn.email_of("users/9") == "nine@example.com"
    assert r.resolve_person("nine@example.com") == "users/9"  # second time from the file


def _page(messages):
    page = MagicMock()
    page.execute.return_value = {"messages": messages}
    return page


if __name__ == "__main__":
    _shim.run(dict(globals()))
