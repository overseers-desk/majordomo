"""Reading Google Chat as a Chat app (``[identity.NAME]``, ``--as NAME``):
the app's key, asked for ``chat.bot`` and ``chat.app.messages.readonly``,
serves spaces, tasks and messages over the Chat API under the sieve, never the
cache, which is the personal login's, and the personal login is never touched. People are named from the name cache alone,
with no People call and no note; what serves only a person's login refuses
naming the app."""

import _shim  # noqa: F401

import json
import types
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from majordomo import api, config, frontdoor, readers
from majordomo.cli import app
from majordomo.errors import MajordomoError, NotFound, ScopeError

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
        {"name": "spaces/BLOCK/messages/B.1", "createTime": "2026-06-01T09:00:00Z",
         "text": "Created a task for @Bob (via Tasks)", "sender": {"name": "users/7", "type": "HUMAN"},
         "annotations": [{"type": "USER_MENTION", "userMention": {"user": {"name": "users/2"}}}]},
    ],
}
BOT = {"me": {"user_id": "users/1"}, "sieve": {"block_spaces": ["spaces/BLOCK"]},
       "identity": {"bot": {"service_account_file": "bot-key.json"}}}

runner = CliRunner()


@pytest.fixture
def google(tmp_path, monkeypatch):
    """The app's key, recording the scopes asked of it; a fake Chat service
    built over it; and a personal login and any other Google API that fail
    the test if reached. ``status`` set makes every message list fail with
    it."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    state = {"scopes": [], "built": [], "status": None}

    def service_account_credentials(ident, scopes):
        state["scopes"].append(scopes)
        return "app-creds"

    def no_login(cfg):
        raise AssertionError("the personal login was touched")

    chat = MagicMock()
    chat.spaces().list().execute.return_value = {"spaces": SPACES}

    def msg_list(parent=None, **kw):
        page = MagicMock()
        if state["status"]:
            err = Exception(f"HTTP {state['status']}")
            err.resp = types.SimpleNamespace(status=state["status"])
            page.execute.side_effect = err
        else:
            page.execute.return_value = {"messages": MSGS.get(parent, [])}
        return page

    chat.spaces().messages().list.side_effect = msg_list

    def build(name, version, credentials=None, cache_discovery=None):
        if name != "chat":
            raise AssertionError(f"the {name} API was reached")
        state["built"].append(credentials)
        return chat

    monkeypatch.setattr(api, "_service_account_credentials", service_account_credentials)
    monkeypatch.setattr(api, "get_credentials", no_login)
    monkeypatch.setattr(api, "_require_google", lambda: (None, None, build))
    config.drain_notes()
    return state


def _bot() -> dict:
    return config.load_config(BOT, identity="bot")


def test_the_app_reads_tasks_and_messages_under_the_sieve(google):
    reader = readers.make_reader(_bot(), "nocache")
    assert reader.source == "nocache"
    tasks = reader.tasks()
    assert [(t["space_name"], t["assignee_user_name"]) for t in tasks] == [("spaces/OK", "users/1")]
    msgs = reader.messages("spaces/OK")
    assert [m["name"] for m in msgs] == ["spaces/OK/messages/T.1", "spaces/OK/messages/T.2"]
    assert [m["sender_display"] for m in msgs] == [None, None]
    with pytest.raises(NotFound):
        reader.messages("spaces/BLOCK")
    assert google["scopes"] == [api.APP_READ_SCOPES]
    assert google["built"] == ["app-creds"]
    assert config.drain_notes() == []


def test_to_me_rows_carry_no_notes_and_reach_no_task_list(google):
    (row,) = readers.make_reader(_bot(), "nocache").tasks(to_user="users/1")
    assert (row["notes"], row["due"]) == (None, None)
    assert config.drain_notes() == []


def test_the_name_cache_still_names_people(google, tmp_path):
    (tmp_path / "majordomo").mkdir()
    (tmp_path / "majordomo" / "people.json").write_text(json.dumps({"people/9": {
        "fetched_at": "2026-06-01T00:00:00Z",
        "person": {"resourceName": "people/9", "names": [
            {"displayName": "Nine Pike", "metadata": {"source": {"type": "PROFILE"}}}]}}}))
    msgs = readers.make_reader(_bot(), "nocache", cache_dir=tmp_path / "majordomo").messages("spaces/OK")
    assert {m["sender_display"] for m in msgs} == {"Nine Pike"}


def test_the_app_reads_the_chat_api_and_refuses_the_cache(google, monkeypatch):
    monkeypatch.setattr(readers.db, "connect", lambda settings: pytest.fail("the cache was opened"))
    assert readers.make_reader(_bot()).source == "nocache"
    for source in ("cache", "live"):
        with pytest.raises(MajordomoError, match="your own login") as ei:
            readers.make_reader(_bot(), source)
        assert not isinstance(ei.value, (ScopeError, NotFound))


def test_what_serves_only_a_person_refuses_naming_the_app(google):
    cfg = _bot()
    cache = readers.CacheReader(None, [], cfg=cfg)
    refusals = {
        "nocache people": lambda: readers.make_reader(cfg, "nocache").people(),
        "cache people": cache.people,
        "live people": lambda: readers.FreshReader(cache, cfg, [], []).people(),
        "owner": lambda: api.owned_spaces(cfg, [], owner=True),
        "attachments": lambda: api.attachments(cfg, [], space="spaces/OK"),
        "login": lambda: frontdoor.login(cfg),
    }
    for what, call in refusals.items():
        with pytest.raises(MajordomoError, match="bot") as ei:
            call()
        assert not isinstance(ei.value, (ScopeError, NotFound)), what


def test_a_refused_message_read_names_the_scope_and_the_approval(google):
    google["status"] = 403
    with pytest.raises(ScopeError) as ei:
        readers.make_reader(_bot(), "nocache").messages("spaces/OK")
    assert ei.value.scopes == [api.APP_MESSAGES_SCOPE]
    assert "bot" in str(ei.value) and "administrator" in str(ei.value)


@pytest.fixture
def bot_home(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        "[me]\nuser_id = 'users/1'\n[sieve]\nblock_spaces = ['spaces/BLOCK']\n"
        "[identity.bot]\nservice_account_file = 'bot-key.json'\n")
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude-here"))
    monkeypatch.delenv("WORLD_AS_OF", raising=False)


def test_cli_as_reads_tasks_and_refuses_people(google, bot_home):
    result = runner.invoke(app, ["--as", "bot", "--nocache", "tasks", "--json"])
    assert result.exit_code == 0, result.output
    assert "spaces/OK/messages/T.2" in result.output and "spaces/BLOCK" not in result.output
    assert google["scopes"] == [api.APP_READ_SCOPES]

    result = runner.invoke(app, ["--as", "bot", "people"])
    assert result.exit_code == 1
    assert "not as the Chat app bot" in result.output


def test_cli_as_reads_the_chat_api_unasked_and_refuses_cache(google, bot_home):
    result = runner.invoke(app, ["--as", "bot", "tasks", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["source"] == "nocache"

    result = runner.invoke(app, ["--as", "bot", "--cache", "tasks"])
    assert result.exit_code == 1
    assert result.output.strip().splitlines() == [
        "majordomo: the cache is read on your own login, not as the Chat app bot; drop --as or --cache."]


if __name__ == "__main__":
    _shim.run(dict(globals()))
