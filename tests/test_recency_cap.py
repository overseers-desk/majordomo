"""The row cap keeps the newest rows, on every backend that has one.

A capped read answers a question about the recent end of a flow ("what was
said", "what was posted"), so the rows the cap discards are the oldest.
Covered here: the cache message report's SQL, the API message reader, and the
attachment listing. `tasks` already orders newest-first and needs no guard.
"""

import _shim  # noqa: F401

from unittest.mock import MagicMock

from majordomo import api, db, reports

SPACE = "spaces/OK"


def _capture(monkeypatch):
    calls: list[tuple[str, list]] = []

    def fake(conn, sql, params=()):
        calls.append((sql, list(params)))
        return []

    monkeypatch.setattr(db, "query", fake)
    return calls


def test_cache_messages_cap_selects_the_newest(monkeypatch):
    calls = _capture(monkeypatch)
    reports.messages(None, [], space=SPACE, limit=7)
    sql, params = [c for c in calls if "googlechat_messages m" in c[0] and "LIMIT" in c[0]][-1]
    inner, outer = sql.split(") r")
    assert "ORDER BY m.create_time DESC" in inner
    assert "ORDER BY r.create_time ASC" in outer
    assert params[-1] == 7


def _msg(n, *, attachment=False):
    m = {"name": f"{SPACE}/messages/T{n}", "createTime": f"2026-0{n}-01T00:00:00Z",
         "sender": {"name": "users/sam", "type": "HUMAN"}, "text": f"m{n}"}
    if attachment:
        m["attachment"] = [{"name": f"{m['name']}/attachments/A", "contentName": f"f{n}.png",
                            "contentType": "image/png", "source": api.UPLOADED,
                            "attachmentDataRef": {"resourceName": f"res-{n}"}}]
    return m


def _chat(messages):
    chat = MagicMock()
    chat.spaces().messages().list.return_value.execute.return_value = {"messages": messages}
    chat.spaces().list.return_value.execute.return_value = {
        "spaces": [{"name": SPACE, "displayName": "Marketing"}]}
    return chat


def test_api_messages_cap_keeps_the_newest():
    chat = _chat([_msg(n) for n in (1, 2, 3, 4, 5)])
    rows = api.NocacheReader(service=chat).messages(space=SPACE, limit=2)
    assert [r["text"] for r in rows] == ["m4", "m5"]


def test_attachments_cap_keeps_the_newest():
    chat = _chat([_msg(n, attachment=True) for n in (1, 2, 3, 4, 5)])
    rows = api.attachments({}, [], space=SPACE, limit=2, service=chat)
    assert [r["content_name"] for r in rows] == ["f4.png", "f5.png"]
