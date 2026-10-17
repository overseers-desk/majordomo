"""The up-front consent check (api.ensure_command_scopes, through
frontdoor.up_front): both front doors run it, once, at the very start of a
command, before any Chat or People call, so a decline or a timeout costs
nothing already spent reading. These tests check the wiring:
each CLI command calls the check with the shape flags its own behaviour
implies, strictly before the read that would otherwise reach Google first,
and shows what the check had to say before reading; test_mcp_server.py
checks each MCP tool the same way. The mechanism itself (one consent for the
union of missing scopes, no second prompt in the same command, the failure
reason) is tested in test_frontdoor.py.
"""

import _shim  # noqa: F401

import types

import pytest
from typer.testing import CliRunner

from majordomo import api, config, readers
from majordomo.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _config_file(tmp_path, monkeypatch):
    toml = tmp_path / "config.toml"
    toml.write_text("[me]\nuser_id = 'users/1'\n")
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    # Keep _claude_command.refresh() (run by every CLI invocation) a no-op:
    # point it at a directory that does not exist, so it never writes.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude-here"))
    monkeypatch.delenv("WORLD_AS_OF", raising=False)


@pytest.fixture
def order(monkeypatch):
    """Every up-front scope check and every call that could reach Google or
    the cache, in call order, so a test can assert the former precedes the
    latter and carries the right shape flags."""
    calls: list[tuple] = []

    def ensure_command_scopes(cfg, **flags):
        calls.append(("consent", flags))

    monkeypatch.setattr(api, "ensure_command_scopes", ensure_command_scopes)
    return calls


def _stub_reader(calls):
    """A duck-typed reader (source + the four report methods) that only
    records that a read happened, never touching a cache DB or the network."""
    reader = types.SimpleNamespace(source="cache")
    for name in ("spaces", "people", "tasks", "messages"):
        # messages(space, ...) takes its space positionally; _name must stay
        # keyword-only so a positional call can never clobber it.
        def method(*args, _name=name, **kw):
            calls.append(("read", _name))
            return []
        setattr(reader, name, method)
    return reader


@pytest.fixture
def stub_reader(order, monkeypatch):
    """readers.make_reader returns the recording stub above."""
    monkeypatch.setattr(readers, "make_reader", lambda cfg, source, **kw: _stub_reader(order))
    return order


# --- CLI ----------------------------------------------------------------


def test_cli_spaces_asks_before_reading(stub_reader):
    result = runner.invoke(app, ["spaces"])
    assert result.exit_code == 0, result.output
    assert stub_reader == [("consent", {"api_read": False, "people": False}), ("read", "spaces")]


def test_cli_shows_the_up_front_notes_before_reading(monkeypatch):
    unshown: list[str] = []

    def ensure_command_scopes(cfg, **flags):
        config.note_once("majordomo: an up-front note.")

    def make_reader(cfg, source, **kw):
        unshown.extend(config.drain_notes())
        return _stub_reader([])

    monkeypatch.setattr(api, "ensure_command_scopes", ensure_command_scopes)
    monkeypatch.setattr(readers, "make_reader", make_reader)
    result = runner.invoke(app, ["spaces", "--json"])
    assert result.exit_code == 0, result.output
    assert unshown == []
    assert "majordomo: an up-front note." in result.output


def test_cli_spaces_nocache_asks_for_api_read(stub_reader):
    result = runner.invoke(app, ["--nocache", "spaces"])
    assert result.exit_code == 0, result.output
    assert stub_reader[0] == ("consent", {"api_read": True, "people": False})


def test_cli_spaces_owner_asks_before_any_membership_read(order, monkeypatch):
    def owned_spaces(cfg, blocked, **kw):
        order.append(("read", "owned_spaces"))
        return []

    monkeypatch.setattr(api, "owned_spaces", owned_spaces)
    result = runner.invoke(app, ["spaces", "--owner"])
    assert result.exit_code == 0, result.output
    assert order == [
        ("consent", {"api_read": True, "owner": True, "people": True}),
        ("read", "owned_spaces"),
    ]


def test_cli_people_asks_for_people_scope_before_reading(stub_reader):
    result = runner.invoke(app, ["people"])
    assert result.exit_code == 0, result.output
    assert stub_reader == [("consent", {"api_read": False, "people": True}), ("read", "people")]


def test_cli_tasks_asks_for_people_scope_before_reading(stub_reader):
    result = runner.invoke(app, ["tasks"])
    assert result.exit_code == 0, result.output
    assert stub_reader == [("consent", {"api_read": False, "people": True, "own": False}), ("read", "tasks")]


def test_cli_tasks_to_me_asks_for_the_own_task_list_too(stub_reader):
    result = runner.invoke(app, ["tasks", "--to-me"])
    assert result.exit_code == 0, result.output
    assert stub_reader[0] == ("consent", {"api_read": False, "people": True, "own": True})


def test_cli_messages_needs_a_target_but_still_asks_first(stub_reader):
    # messages with none of --space/--thread/--person given returns no rows,
    # but the up-front check must still have run before that empty read.
    result = runner.invoke(app, ["messages"])
    assert result.exit_code == 0, result.output
    assert stub_reader == [("consent", {"api_read": False, "people": True}), ("read", "messages")]


def test_cli_attachments_asks_api_read_and_people_before_reading(order, monkeypatch):
    def attachments(cfg, blocked, **kw):
        order.append(("read", "attachments"))
        return []

    monkeypatch.setattr(api, "attachments", attachments)
    result = runner.invoke(app, ["attachments", "--space", "spaces/A"])
    assert result.exit_code == 0, result.output
    assert order == [("consent", {"api_read": True, "people": True}), ("read", "attachments")]


def test_cli_send_asks_only_the_send_scope_before_sending(order, monkeypatch):
    def send(cfg, **kw):
        order.append(("read", "send"))
        return {"name": "spaces/A/messages/1"}

    monkeypatch.setattr(api, "send", send)
    result = runner.invoke(app, ["send", "--space", "spaces/A", "hi"])
    assert result.exit_code == 0, result.output
    assert order == [("consent", {"send": True}), ("read", "send")]


# --- --as / identity --------------------------------------------------------


@pytest.fixture
def bot_config(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[me]\nuser_id = 'users/1'\n[identity.bot]\nservice_account_file = 'bot-key.json'\n")


def test_cli_as_reads_a_report_as_the_identity(bot_config, monkeypatch):
    reads: list[tuple] = []
    seen = {}

    def make_reader(cfg, source, **kw):
        seen["identity"] = config.identity(cfg)
        return _stub_reader(reads)

    monkeypatch.setattr(readers, "make_reader", make_reader)
    result = runner.invoke(app, ["--as", "bot", "tasks"])
    assert result.exit_code == 0, result.output
    assert seen["identity"]["name"] == "bot"
    assert reads == [("read", "tasks")]


def test_cli_as_sends_as_the_identity(bot_config, monkeypatch):
    seen = {}

    def send(cfg, **kw):
        seen["identity"] = config.identity(cfg)
        return {"name": "spaces/A/messages/1"}

    monkeypatch.setattr(api, "send", send)
    result = runner.invoke(app, ["--as", "bot", "send", "--space", "spaces/A", "hi"])
    assert result.exit_code == 0, result.output
    assert seen["identity"]["name"] == "bot"


def test_cli_as_an_unknown_name_stops_before_sending(bot_config, monkeypatch):
    monkeypatch.setattr(api, "send", lambda *a, **k: pytest.fail("sent"))
    result = runner.invoke(app, ["--as", "nosuch", "send", "--space", "spaces/A", "hi"])
    assert result.exit_code != 0
    assert "[identity.nosuch]" in result.output


def test_cli_as_does_not_apply_to_mcp():
    result = runner.invoke(app, ["--as", "bot", "mcp"])
    assert result.exit_code == 2
    assert "identity on each call" in result.output


if __name__ == "__main__":
    _shim.run(dict(globals()))
