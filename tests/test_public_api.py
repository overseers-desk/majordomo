"""majordomo as a library: the names it exports, and ``tasks``/``messages``
reading with only what they are handed (a config dict, credentials, a
directory for the name cache) over a fake Chat API, and ``send`` taking its
sieve from the config alone. A guard fails every test
here that reads the home directory or the host's environment, so these run
without the test shim and with no environment set up."""

import copy
import os
import sys
import types
from collections.abc import MutableMapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import majordomo
from majordomo import api, config, db, roster

_ENV_GUARDED = ("HOME", "WORLD_AS_OF", "CI", "DISPLAY")
_ENV_GUARDED_PREFIXES = ("XDG_", "MYSQL_")
_PACKAGE = os.path.dirname(majordomo.__file__) + os.sep


def _by_the_library() -> bool:
    """Whether majordomo's own code is on the stack: pytest reads ``CI``
    itself when it words a failed assertion."""
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_filename.startswith(_PACKAGE):
            return True
        frame = frame.f_back
    return False


class _Environ(MutableMapping):
    """``os.environ``, refusing (and recording) the library's read of a
    guarded variable."""

    def __init__(self, real, refuse):
        self._real, self._refuse = real, refuse

    def _check(self, key):
        if isinstance(key, str) and (key in _ENV_GUARDED or key.startswith(_ENV_GUARDED_PREFIXES)):
            self._refuse(f"${key}")

    def __getitem__(self, key):
        self._check(key)
        return self._real[key]

    def __contains__(self, key):
        self._check(key)
        return key in self._real

    def __setitem__(self, key, value):
        self._real[key] = value

    def __delitem__(self, key):
        del self._real[key]

    def __iter__(self):
        return iter(self._real)

    def __len__(self):
        return len(self._real)


@pytest.fixture(autouse=True)
def home_guard(tmp_path, monkeypatch):
    """The library's every read of the home directory, a ``~`` path, or a
    guarded variable raises, and is recorded too, so one the library swallows
    still fails."""
    reads: list[str] = []

    def refuse(what):
        if _by_the_library():
            reads.append(what)
            raise AssertionError(f"the library read {what}")

    real_home, real_expanduser = Path.home, os.path.expanduser

    def home(cls):
        refuse("Path.home()")
        return real_home()

    def expanduser(path):
        p = os.fspath(path)
        if p[:1] in ("~", b"~"):
            refuse(f"expanduser({p!r})")
        return real_expanduser(p)

    config.drain_notes()
    monkeypatch.setattr(Path, "home", classmethod(home))
    monkeypatch.setattr(os.path, "expanduser", expanduser)
    monkeypatch.setattr(os, "environ", _Environ(os.environ, refuse))
    yield
    assert reads == []


ME = "users/1"
SPACES = [
    {"name": "spaces/OK", "displayName": "Kitchen Ops", "spaceType": "SPACE"},
    {"name": "spaces/TWO", "displayName": "Kitchen Ops Two", "spaceType": "SPACE"},
    {"name": "spaces/BLOCK", "displayName": "Private", "spaceType": "SPACE"},
]
MSGS = {
    "spaces/OK": [
        {"name": "spaces/OK/messages/T.1", "createTime": "2026-06-01T08:00:00Z", "text": "ship the thing",
         "sender": {"name": "users/9", "type": "HUMAN"}},
        {"name": "spaces/OK/messages/T.2", "createTime": "2026-06-01T08:05:00Z",
         "text": "Created a task for @Ada (via Tasks)", "sender": {"name": "users/9", "type": "HUMAN"},
         "annotations": [{"type": "USER_MENTION", "userMention": {"user": {"name": ME}}}]},
        {"name": "spaces/OK/messages/U.1", "createTime": "2026-06-01T09:00:00Z",
         "text": "Created a task for @Bo (via Tasks)", "sender": {"name": "users/9", "type": "HUMAN"},
         "annotations": [{"type": "USER_MENTION", "userMention": {"user": {"name": "users/2"}}}]},
    ],
    "spaces/TWO": [],
    "spaces/BLOCK": [
        {"name": "spaces/BLOCK/messages/B.1", "createTime": "2026-06-01T09:00:00Z", "text": "private",
         "sender": {"name": "users/7", "type": "HUMAN"}},
    ],
}
NAMES = {"people/9": "Nine Pike", "people/1": "Ada Quill", "people/2": "Bo Vance"}
OWN_TASK = {"id": "a", "title": "ship the thing", "notes": "by Friday", "updated": "2026-06-01T08:05:01.000Z",
            "assignmentInfo": {"surfaceType": "SPACE", "spaceInfo": {"space": "spaces/OK"},
                               "linkToTask": "https://mail.google.com/chat/#chat/space/OK/T/T"}}
SOURCE = {"me": {"user_id": ME}, "sieve": {"block_spaces": ["spaces/BLOCK"]},
          "identity": {"bot": {"service_account_file": "/nowhere/bot-key.json"}}}
CHAT_ONLY = types.SimpleNamespace(scopes=list(api.CHAT_READ_SCOPES))
EVERY = types.SimpleNamespace(scopes=list(api.LOGIN_SCOPES))


def _page(body=None, status=None):
    page = MagicMock()
    if status:
        err = Exception(f"HTTP {status}")
        err.resp = types.SimpleNamespace(status=status)
        page.execute.side_effect = err
    else:
        page.execute.return_value = body
    return page


@pytest.fixture
def google(monkeypatch):
    """A fake Chat, People and Tasks API behind ``api._require_google``,
    recording the message-list filters, the People batches and each API
    built."""
    state = {"filters": [], "people_calls": 0, "built": []}
    chat = MagicMock()
    chat.spaces().list.side_effect = lambda **kw: _page({"spaces": SPACES})

    def msg_list(parent=None, filter="", **kw):
        state["filters"].append(filter)
        return _page({"messages": MSGS[parent]}) if parent in MSGS else _page(status=404)

    chat.spaces().messages().list.side_effect = msg_list

    people = MagicMock()

    def batch_get(resourceNames=None, **kw):
        state["people_calls"] += 1
        return _page({"responses": [
            {"requestedResourceName": n, "person": {"resourceName": n, "names": [
                {"displayName": NAMES[n], "metadata": {"source": {"type": "PROFILE"}}}]}}
            for n in resourceNames if n in NAMES]})

    people.people().getBatchGet.side_effect = batch_get

    tasks = MagicMock()
    tasks.tasklists().list.side_effect = lambda **kw: _page({"items": [{"id": "L"}]})
    tasks.tasks().list.side_effect = lambda **kw: _page({"items": [OWN_TASK]})

    def build(name, version, credentials=None, cache_discovery=None):
        state["built"].append(name)
        return {"chat": chat, "people": people, "tasks": tasks}[name]

    monkeypatch.setattr(api, "_require_google", lambda: (None, None, build))
    monkeypatch.setattr(db, "connect", lambda settings: pytest.fail("the cache was opened"))
    return state


def _cfg(**kw) -> dict:
    return majordomo.load_config(SOURCE, **kw)


# --- the names ------------------------------------------------------------------

def test_the_public_names():
    assert set(majordomo.__all__) == {"load_config", "make_reader", "tasks", "messages", "send",
                                      "drain_notes", "MajordomoError", "ScopeError", "NotFound"}
    assert all(hasattr(majordomo, name) for name in majordomo.__all__)
    assert callable(majordomo.dates.resolve)


def test_the_refusal_classes():
    assert issubclass(majordomo.MajordomoError, Exception)
    assert issubclass(majordomo.ScopeError, majordomo.MajordomoError)
    assert issubclass(majordomo.NotFound, majordomo.MajordomoError)
    assert majordomo.ScopeError("short", scopes=[api.TASKS_SCOPE]).scopes == [api.TASKS_SCOPE]


def test_load_config_takes_a_copy_of_its_dict_with_the_identity_and_the_bound():
    before = copy.deepcopy(SOURCE)
    cfg = _cfg(identity="bot", world_as_of="2026-07-12T17:07:00+10:00")
    cfg["sieve"]["block_spaces"].append("spaces/MORE")
    assert SOURCE == before
    assert config.identity(cfg)["name"] == "bot"
    assert majordomo.dates.resolve("7d", cfg=cfg) == (datetime(2026, 7, 5, 7, 7), datetime(2026, 7, 12, 7, 7))


# --- reading ----------------------------------------------------------------------

def test_messages_are_dicts_in_naive_utc_each_naming_its_sender(google, tmp_path):
    rows = majordomo.messages(_cfg(), "spaces/OK", source="nocache", credentials=CHAT_ONLY, cache_dir=tmp_path)
    assert [r["name"] for r in rows] == [m["name"] for m in MSGS["spaces/OK"]]
    for r in rows:
        assert type(r) is dict and r["create_time"].tzinfo is None
        assert r["sender_name"] == "users/9" and r["sender_display"] is None
    assert roster.LOGIN_NOTE in majordomo.drain_notes()


def test_tasks_are_dicts_in_naive_utc_each_naming_its_assignee(google, tmp_path):
    rows = majordomo.tasks(_cfg(), source="nocache", credentials=CHAT_ONLY, cache_dir=tmp_path)
    assert [r["assignee_user_name"] for r in rows] == ["users/2", ME]
    assert all(type(r) is dict and r["created_at"].tzinfo is None for r in rows)


def test_a_reader_names_the_source_that_serves(google, monkeypatch):
    def down(settings):
        raise majordomo.MajordomoError("majordomo: no cache database")

    monkeypatch.setattr(db, "connect", down)
    assert majordomo.make_reader(_cfg(), credentials=CHAT_ONLY).source == "nocache"
    with pytest.raises(majordomo.MajordomoError, match="cache unreachable"):
        majordomo.make_reader(_cfg(), "cache", credentials=CHAT_ONLY)


def test_aware_bounds_are_converted_and_naive_ones_taken_as_utc(google):
    ten = timezone(timedelta(hours=10))
    majordomo.messages(_cfg(), "spaces/OK", start=datetime(2026, 6, 1, 10, tzinfo=ten),
                       end=datetime(2026, 6, 2, 10, tzinfo=ten), source="nocache", credentials=CHAT_ONLY)
    majordomo.messages(_cfg(), "spaces/OK", start=datetime(2026, 6, 1), end=datetime(2026, 6, 2),
                       source="nocache", credentials=CHAT_ONLY)
    utc = 'createTime > "2026-06-01T00:00:00Z" AND createTime < "2026-06-02T00:00:00Z"'
    assert google["filters"] == [utc, utc]


def test_limit_none_is_the_reader_s_own_cap(google):
    def read(**kw):
        return majordomo.messages(_cfg(), "spaces/OK", source="nocache", credentials=CHAT_ONLY, **kw)

    assert len(read()) == len(read(limit=None)) == 3
    assert [r["name"] for r in read(limit=1)] == ["spaces/OK/messages/U.1"]


def test_a_blocked_space_answers_as_an_absent_one(google):
    refusals = []
    for space in ("spaces/BLOCK", "spaces/GONE"):
        for read in (majordomo.messages, majordomo.tasks):
            with pytest.raises(majordomo.NotFound) as ei:
                read(_cfg(), space=space, source="nocache", credentials=CHAT_ONLY)
            refusals.append(str(ei.value).replace(space, "spaces/X"))
    assert len(set(refusals)) == 1


def test_an_ambiguous_name_is_refused_but_not_as_not_found(google):
    with pytest.raises(majordomo.MajordomoError, match="matches 2 spaces") as ei:
        majordomo.messages(_cfg(), "kitchen", source="nocache", credentials=CHAT_ONLY)
    assert not isinstance(ei.value, majordomo.NotFound)


def test_names_are_kept_in_cache_dir_and_fetched_per_call_without_one(google, tmp_path, monkeypatch):
    def read(cache_dir):
        rows = majordomo.messages(_cfg(), "spaces/OK", source="nocache", credentials=EVERY, cache_dir=cache_dir)
        assert {r["sender_display"] for r in rows} == {"Nine Pike"}

    read(tmp_path)
    read(str(tmp_path))
    assert google["people_calls"] == 1
    assert {p.name for p in tmp_path.iterdir()} == {"people.json", "spaces.json"}

    monkeypatch.setattr(roster, "_load", lambda path: pytest.fail(f"read {path}"))
    monkeypatch.setattr(roster, "_write", lambda path, data: pytest.fail(f"wrote {path}"))
    read(None)
    read(None)
    assert google["people_calls"] == 3


def test_only_the_configured_subject_s_tasks_read_the_task_list(google):
    def read(to_user):
        return majordomo.tasks(_cfg(), to_user=to_user, source="nocache", credentials=EVERY)

    (theirs,) = read("users/2")
    assert (theirs["notes"], theirs["due"]) == (None, None)
    assert "tasks" not in google["built"]
    (mine,) = read(ME)
    assert mine["notes"] == "by Friday"


def test_an_identity_reads_the_chat_api_and_refuses_the_cache(google):
    cfg = _cfg(identity="bot")
    assert majordomo.make_reader(cfg, credentials=CHAT_ONLY).source == "nocache"
    assert majordomo.tasks(cfg, credentials=CHAT_ONLY)
    assert "people" not in google["built"] and majordomo.drain_notes() == []
    for source in ("cache", "live"):
        with pytest.raises(majordomo.MajordomoError, match="your own login") as ei:
            majordomo.tasks(cfg, source=source, credentials=CHAT_ONLY)
        assert not isinstance(ei.value, (majordomo.NotFound, majordomo.ScopeError))


# --- sending ----------------------------------------------------------------------

def test_send_refuses_a_space_the_cfg_s_sieve_blocks_and_takes_no_other_list(google):
    for target in ({"space": "spaces/BLOCK"}, {"thread": "spaces/BLOCK/messages/B.1"}):
        with pytest.raises(majordomo.NotFound, match="spaces/BLOCK: not found"):
            majordomo.send(_cfg(), text="hi", **target)
    with pytest.raises(TypeError):
        majordomo.send(_cfg(), [], space="spaces/BLOCK", text="hi")
    with pytest.raises(TypeError):
        majordomo.send(_cfg(), blocked=[], space="spaces/BLOCK", text="hi")
    assert google["built"] == []
