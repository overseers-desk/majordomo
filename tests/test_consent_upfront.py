"""The up-front consent check (api.ensure_command_scopes): both front doors
call it, once, at the very start of a command, before any Chat or People
call, so a decline or a timeout costs nothing already spent reading. The
motivating case: `spaces --owner` used to open the consent page only after
about 50s of membership reads under Google's per-minute quota, wasting all of
it on a decline. These tests check the wiring: each CLI command and MCP tool
calls the check with the shape flags its own behaviour implies, strictly
before the read that would otherwise reach Google first. The mechanism itself
(one consent for the union of missing scopes, no second prompt in the same
command, the failure reason) is tested in test_consent.py.
"""

import _shim  # noqa: F401

import types

import pytest
from typer.testing import CliRunner

from majordomo import api, config, readers
from majordomo.cli import app
from majordomo.mcp_server import create_server

runner = CliRunner()


@pytest.fixture(autouse=True)
def _config_file(tmp_path, monkeypatch):
    toml = tmp_path / "config.toml"
    toml.write_text("[me]\nuser_id = 'users/1'\n")
    monkeypatch.setattr(config, "CONFIG_TOML", toml)
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
    monkeypatch.setattr(readers, "make_reader", lambda cfg, source: _stub_reader(order))
    return order


# --- CLI ----------------------------------------------------------------


def test_cli_spaces_asks_before_reading(stub_reader):
    result = runner.invoke(app, ["spaces"])
    assert result.exit_code == 0, result.output
    assert stub_reader == [("consent", {"api_read": False, "people": False}), ("read", "spaces")]


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
    assert stub_reader == [("consent", {"api_read": False, "people": True}), ("read", "tasks")]


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
    def send(cfg, blocked, **kw):
        order.append(("read", "send"))
        return {"name": "spaces/A/messages/1"}

    monkeypatch.setattr(api, "send", send)
    result = runner.invoke(app, ["send", "--space", "spaces/A", "hi"])
    assert result.exit_code == 0, result.output
    assert order == [("consent", {"send": True}), ("read", "send")]


# --- MCP ------------------------------------------------------------------


def _tool(name):
    return create_server()._tool_manager.get_tool(name).fn


def test_mcp_spaces_asks_before_reading(order, monkeypatch):
    monkeypatch.setattr(readers, "make_reader", lambda cfg, source: _stub_reader(order))
    out = _tool("spaces")()
    assert out["rows"] == []
    assert order == [("consent", {"api_read": False}), ("read", "spaces")]


def test_mcp_spaces_owner_asks_before_any_membership_read(order, monkeypatch):
    def owned_spaces(cfg, blocked, **kw):
        order.append(("read", "owned_spaces"))
        return []

    monkeypatch.setattr(api, "owned_spaces", owned_spaces)
    out = _tool("spaces")(owner=True)
    assert out["rows"] == []
    assert order == [
        ("consent", {"api_read": True, "owner": True, "people": True}),
        ("read", "owned_spaces"),
    ]


def test_mcp_people_asks_for_people_scope_before_reading(order, monkeypatch):
    monkeypatch.setattr(readers, "make_reader", lambda cfg, source: _stub_reader(order))
    out = _tool("people")()
    assert out["rows"] == []
    assert order == [("consent", {"api_read": False, "people": True}), ("read", "people")]


def test_mcp_tasks_asks_for_people_scope_before_reading(order, monkeypatch):
    monkeypatch.setattr(readers, "make_reader", lambda cfg, source: _stub_reader(order))
    out = _tool("tasks")()
    assert out["rows"] == []
    assert order == [("consent", {"api_read": False, "people": True}), ("read", "tasks")]


def test_mcp_messages_asks_for_people_scope_before_reading(order, monkeypatch):
    monkeypatch.setattr(readers, "make_reader", lambda cfg, source: _stub_reader(order))
    out = _tool("messages")()
    assert out["rows"] == []
    assert order == [("consent", {"api_read": False, "people": True}), ("read", "messages")]


def test_mcp_attachments_asks_api_read_and_people_before_reading(order, monkeypatch):
    def attachments(cfg, blocked, **kw):
        order.append(("read", "attachments"))
        return []

    monkeypatch.setattr(api, "attachments", attachments)
    out = _tool("attachments")(space="spaces/A")
    assert out["rows"] == []
    assert order == [("consent", {"api_read": True, "people": True}), ("read", "attachments")]


def test_mcp_send_asks_only_the_send_scope_before_sending(order, monkeypatch):
    def send(cfg, blocked, **kw):
        order.append(("read", "send"))
        return {"name": "spaces/A/messages/1"}

    monkeypatch.setattr(api, "send", send)
    out = _tool("send")(space="spaces/A", text="hi")
    assert out["name"] == "spaces/A/messages/1"
    assert order == [("consent", {"send": True}), ("read", "send")]


if __name__ == "__main__":
    _shim.run(dict(globals()))
