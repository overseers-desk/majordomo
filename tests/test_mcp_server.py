"""The MCP front door: each tool asks for the Google permissions its shape
needs (api.ensure_command_scopes, through frontdoor.up_front) before the read
that would otherwise reach Google first, and a core refusal reaches the
client as its one line."""

import pytest

pytest.importorskip("mcp")

import _shim  # noqa: E402,F401

import asyncio  # noqa: E402
import types  # noqa: E402

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError  # noqa: E402

from majordomo import api, config, readers  # noqa: E402
from majordomo.mcp_server import create_server  # noqa: E402


@pytest.fixture(autouse=True)
def _config_file(tmp_path, monkeypatch):
    toml = tmp_path / "config.toml"
    toml.write_text("[me]\nuser_id = 'users/1'\n")
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.delenv("WORLD_AS_OF", raising=False)


@pytest.fixture
def order(monkeypatch):
    """Every up-front scope check and every call that could reach Google or
    the cache, in call order."""
    calls: list[tuple] = []

    def ensure_command_scopes(cfg, **flags):
        calls.append(("consent", flags))

    monkeypatch.setattr(api, "ensure_command_scopes", ensure_command_scopes)
    return calls


def _stub_reader(calls):
    reader = types.SimpleNamespace(source="cache")
    for name in ("spaces", "people", "tasks", "messages"):
        # messages(space, ...) takes its space positionally; _name must stay
        # keyword-only so a positional call can never clobber it.
        def method(*args, _name=name, **kw):
            calls.append(("read", _name))
            return []
        setattr(reader, name, method)
    return reader


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
    assert order == [("consent", {"api_read": False, "people": True, "own": False}), ("read", "tasks")]


def test_mcp_tasks_to_me_asks_for_the_own_task_list_too(order, monkeypatch):
    monkeypatch.setattr(readers, "make_reader", lambda cfg, source: _stub_reader(order))
    _tool("tasks")(to_me=True)
    assert order[0] == ("consent", {"api_read": False, "people": True, "own": True})


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


def test_mcp_send_sends_as_the_named_identity(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        "[me]\nuser_id = 'users/1'\n[identity.bot]\nservice_account_file = 'bot-key.json'\n")
    seen = {}

    def send(cfg, blocked, **kw):
        seen["identity"] = config.identity(cfg)
        return {"name": "spaces/A/messages/1"}

    monkeypatch.setattr(api, "send", send)
    out = _tool("send")(space="spaces/A", text="hi", identity="bot")
    assert out["name"] == "spaces/A/messages/1"
    assert seen["identity"]["name"] == "bot"


def test_a_refusal_reaches_the_client_as_its_one_line(tmp_path, monkeypatch):
    # The SDK shows the client a ToolError's text; anything else it reports
    # only as "Error executing tool spaces", a crash.
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "absent")
    with pytest.raises(ToolError) as ei:
        asyncio.run(create_server().call_tool("spaces", {}))
    assert not isinstance(ei.value, UnexpectedToolError)
    assert str(ei.value) == f"Error executing tool spaces: majordomo: no config at {tmp_path / 'absent' / 'config.toml'}"


if __name__ == "__main__":
    _shim.run(dict(globals()))
