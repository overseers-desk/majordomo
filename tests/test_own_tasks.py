"""The fill of tasks assigned to the signed-in person from their own Google
Tasks list (api.own_tasks, readers.fill_own): the index the API answer
becomes, and the join onto task rows, over a fake Tasks service."""

import _shim  # noqa: F401

from datetime import datetime, timezone
from unittest.mock import MagicMock

from majordomo import api, readers

LINK = "https://mail.google.com/chat/#chat/space/"
MADE_FROM_MESSAGE = {"id": "a", "title": "ship the thing", "notes": "by Friday", "due": "2026-06-05T00:00:00.000Z",
                     "updated": "2026-06-01T08:05:01.000Z",
                     "assignmentInfo": {"surfaceType": "SPACE", "spaceInfo": {"space": "spaces/OK"},
                                        "linkToTask": LINK + "OK/T/T"}}
FRESH = {"id": "b", "title": "book the venue", "updated": "2026-06-02T09:00:00.500Z",
         "assignmentInfo": {"surfaceType": "SPACE", "spaceInfo": {"space": "spaces/OK"}, "linkToTask": LINK + "OK"}}
FRESH_TWIN = dict(FRESH, id="c", title="or the other venue", updated="2026-06-02T09:00:02.000Z")
FROM_A_DOC = {"id": "d", "title": "doc task", "updated": "2026-06-02T09:00:00.000Z",
              "assignmentInfo": {"surfaceType": "DOCUMENT", "linkToTask": "https://docs.google.com/x"}}


def _page(body):
    req = MagicMock()
    req.execute.return_value = body
    return req


def _fake_tasks(lists: dict):
    svc = MagicMock()
    svc.tasklists().list.side_effect = lambda **kw: _page({"items": [{"id": k, "title": k} for k in lists]})
    svc.tasks().list.side_effect = lambda tasklist=None, **kw: _page({"items": lists.get(tasklist, [])})
    return svc


def test_own_tasks_indexes_every_list_by_thread_or_space():
    svc = _fake_tasks({"My Tasks": [MADE_FROM_MESSAGE, FRESH, FROM_A_DOC], "Other": [FRESH_TWIN]})
    index = api.own_tasks({}, service=svc)
    assert index["by_thread"] == {("spaces/OK", "T"): MADE_FROM_MESSAGE}
    assert [t["id"] for _, t in index["by_space"]["spaces/OK"]] == ["b", "c"]     # the Docs task is left out
    _, kwargs = svc.tasks().list.call_args_list[0]
    assert kwargs["showAssigned"] is True                                        # the flag the 2025 test omitted


def _row(name, created, title=None, space="spaces/OK"):
    return {"source_message_name": name, "space_name": space, "title": title,
            "created_at": datetime.fromisoformat(created).replace(tzinfo=timezone.utc)}


def test_fill_own_joins_by_thread_then_by_space_and_time():
    index = api.own_tasks({}, service=_fake_tasks({"My Tasks": [MADE_FROM_MESSAGE, FRESH]}))
    rows = [_row("spaces/OK/messages/T.2", "2026-06-01T08:05:00", title="ship the thing"),   # by thread
            _row("spaces/OK/messages/F.1", "2026-06-02T09:00:00"),                            # fresh: space and time
            _row("spaces/OK/messages/G.1", "2026-06-02T12:00:00"),                            # nothing that close
            _row("spaces/ELSE/messages/T.2", "2026-06-01T08:05:00", space="spaces/ELSE")]     # another space
    readers.fill_own(rows, lambda: index)
    assert (rows[0]["title"], rows[0]["notes"], rows[0]["due"]) == ("ship the thing", "by Friday", "2026-06-05T00:00:00.000Z")
    assert (rows[1]["title"], rows[1]["notes"]) == ("book the venue", None)
    assert [(r["title"], r["notes"], r["due"]) for r in rows[2:]] == [(None, None, None)] * 2


def test_fill_own_leaves_an_ambiguous_time_match_alone_and_none_without_a_list():
    index = api.own_tasks({}, service=_fake_tasks({"My Tasks": [FRESH, FRESH_TWIN]}))
    (row,) = readers.fill_own([_row("spaces/OK/messages/F.1", "2026-06-02T09:00:01")], lambda: index)
    assert (row["title"], row["notes"], row["due"]) == (None, None, None)        # two tasks within 5 s: no guess
    (row,) = readers.fill_own([_row("spaces/OK/messages/F.1", "2026-06-02T09:00:01")], None)
    assert (row["notes"], row["due"]) == (None, None)                           # keys present, nothing filled


if __name__ == "__main__":
    _shim.run(dict(globals()))
