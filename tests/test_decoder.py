"""Decoder unit tests — the coord `decode.test.js` cases, ported to Python."""

import _shim  # noqa: F401

from majordomo import decoder

TASK = {
    "name": "spaces/AAAA/messages/BBBB.CCCC",
    "createTime": "2026-06-20T08:15:00.000000Z",
    "text": "Created a task for @John Doe (via Tasks)",
    "annotations": [
        {"type": "USER_MENTION", "startIndex": 19, "length": 9,
         "userMention": {"user": {"name": "users/123456789", "type": "HUMAN"}, "type": "MENTION"}},
    ],
}


def test_space_of_message():
    assert decoder.space_of_message("spaces/AAAA/messages/BBBB.CCCC") == "spaces/AAAA"
    assert decoder.space_of_message("garbage") is None


def test_is_task_creation():
    assert decoder.is_task_creation(TASK)
    assert not decoder.is_task_creation({"text": "Completed a task (via Tasks)"})
    assert not decoder.is_task_creation({"text": "thanks, on it"})


def test_assignee_from_text():
    assert decoder.assignee_from_text("Created a task for @John Doe (via Tasks)") == "John Doe"
    assert decoder.assignee_from_text("Created a task for @John Doe (P) (via Tasks)") == "John Doe"
    assert decoder.assignee_from_text("Created a task (via Tasks)") is None


def test_assignee_user_from_annotations():
    assert decoder.assignee_user_from_annotations(TASK["annotations"]) == "users/123456789"
    scan = [{"type": "X"}, {"type": "USER_MENTION", "userMention": {"user": {"name": "users/9"}}}]
    assert decoder.assignee_user_from_annotations(scan) == "users/9"
    assert decoder.assignee_user_from_annotations(None) is None


def test_decode_task_full():
    assert decoder.decode_task(TASK) == {
        "source_message_name": "spaces/AAAA/messages/BBBB.CCCC",
        "space_name": "spaces/AAAA",
        "assignee_user_name": "users/123456789",
        "assignee_display": "John Doe",
        "title": None,
        "created_at": "2026-06-20T08:15:00.000000Z",
    }


def test_decode_task_assignee_less():
    msg = {"name": "spaces/AAAA/messages/HH.II", "createTime": "2026-06-20T09:00:00.000000Z",
           "text": "Created a task (via Tasks)"}
    t = decoder.decode_task(msg)
    assert t["assignee_user_name"] is None and t["assignee_display"] is None


def test_decode_task_rejects_non_tasks():
    assert decoder.decode_task({"name": "x", "text": "Completed a task (via Tasks)"}) is None
    assert decoder.decode_task({"name": "x", "text": "thanks"}) is None
    assert decoder.decode_task(None) is None
    assert decoder.decode_task({"text": "Created a task (via Tasks)"}) is None  # no name


def test_space_fallback_when_absent():
    msg = {"name": "spaces/ZZZ/messages/M.N", "createTime": "t", "text": "Created a task for @X (via Tasks)"}
    assert decoder.decode_task(msg)["space_name"] == "spaces/ZZZ"


def test_recover_titles_from_prior_plain_message():
    tasks = [decoder.decode_task({"name": "spaces/A/messages/T.2", "createTime": "2026-06-20T08:15:00.000000Z",
                                  "text": "Created a task for @X (via Tasks)"})]
    messages = [
        {"name": "spaces/A/messages/T.1", "createTime": "2026-06-20T08:00:00.000000Z", "text": "please upload the boarding pass"},
        {"name": "spaces/A/messages/T.2", "createTime": "2026-06-20T08:15:00.000000Z", "text": "Created a task for @X (via Tasks)"},
        {"name": "spaces/A/messages/T.3", "createTime": "2026-06-20T09:00:00.000000Z", "text": "a later message"},
    ]
    decoder.recover_titles(tasks, messages)
    assert tasks[0]["title"] == "please upload the boarding pass"


def test_recover_titles_none_when_no_prior_plain():
    tasks = [decoder.decode_task({"name": "spaces/A/messages/T.2", "createTime": "2026-06-20T08:15:00.000000Z",
                                  "text": "Created a task for @X (via Tasks)"})]
    decoder.recover_titles(tasks, [{"name": "spaces/A/messages/T.3", "createTime": "2026-06-20T09:00:00.000000Z", "text": "after"}])
    assert tasks[0]["title"] is None


if __name__ == "__main__":
    _shim.run(dict(globals()))
