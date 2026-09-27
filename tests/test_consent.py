"""A token short of a scope a command needs: the command opens Google's
consent flow itself when someone can answer it, then carries on; with nobody
there (no display and no terminal, CI, a WORLD_AS_OF replay) it does not, and
a declined or failed consent falls back softly. A refusal is not remembered:
the next call asks again. Everything the flow prints goes to stderr."""

import _shim  # noqa: F401

import sys
import types
from unittest.mock import MagicMock

import pytest

from majordomo import api, config, roster

CHAT_ONLY = ["https://www.googleapis.com/auth/chat.spaces.readonly"]


def _creds(scopes):
    return types.SimpleNamespace(scopes=list(scopes), valid=True)


@pytest.fixture
def world(monkeypatch):
    """Credentials that gain every scope once the consent flow succeeds, a
    recorded flow, a People build, and a display to open a browser on."""
    state = {"scopes": CHAT_ONLY, "consents": [], "declines": False}

    def consent(cfg, *, open_browser=True, timeout=None):
        state["consents"].append({"open_browser": open_browser, "timeout": timeout})
        if state["declines"]:
            raise SystemExit("majordomo: login failed or was not completed")
        state["scopes"] = api.LOGIN_SCOPES
        return "token.json"

    monkeypatch.setattr(api, "get_credentials", lambda cfg: _creds(state["scopes"]))
    monkeypatch.setattr(api, "_consent", consent)
    monkeypatch.setattr(api, "_require_google", lambda: (None, None, lambda *a, **k: "PEOPLE"))
    monkeypatch.setattr(api, "consent_mode", lambda: "browser")
    monkeypatch.delenv(config.WORLD_AS_OF_ENV, raising=False)
    config.drain_notes()
    return state


def test_scope_missing_runs_the_flow_and_the_command_carries_on(world):
    assert api.people_service({}) == "PEOPLE"
    assert world["consents"] == [{"open_browser": True, "timeout": api.CONSENT_TIMEOUT}]
    notes = config.drain_notes()
    assert len(notes) == 1 and "needs more Google permissions" in notes[0]


def test_a_token_that_has_the_scopes_asks_nothing(world):
    world["scopes"] = api.LOGIN_SCOPES
    assert api.people_service({}) == "PEOPLE"
    assert world["consents"] == []


def test_nobody_to_answer_means_no_flow_and_a_soft_note(world, monkeypatch):
    monkeypatch.setattr(api, "consent_mode", lambda: None)
    assert api.people_service({}) is None
    assert world["consents"] == []
    assert config.drain_notes() == [roster.LOGIN_NOTE]


def test_declined_falls_back_softly_and_the_next_call_asks_again(world):
    world["declines"] = True
    assert api.people_service({}) is None
    assert roster.LOGIN_NOTE in config.drain_notes()
    assert api.people_service({}) is None
    assert len(world["consents"]) == 2


def test_world_as_of_never_opens_the_flow(world, monkeypatch):
    monkeypatch.setenv(config.WORLD_AS_OF_ENV, "2026-01-01T00:00:00+00:00")
    assert api.people_service({}) is None
    assert world["consents"] == []


def test_a_terminal_without_a_display_gets_the_link_not_a_browser(world, monkeypatch):
    monkeypatch.setattr(api, "consent_mode", lambda: "url")
    assert api.people_service({}) == "PEOPLE"
    assert world["consents"][0]["open_browser"] is False


def test_one_command_resolving_many_ids_asks_once(world, tmp_path):
    world["declines"] = True
    ros = roster.Roster(tmp_path, lookup=lambda: api.people_service({}))
    assert ros.names(["users/1", "users/2"]) == {"users/1": None, "users/2": None}
    assert ros.names(["users/3"]) == {"users/3": None}
    assert len(world["consents"]) == 1


def test_send_short_of_its_scope_consents_then_sends(world, monkeypatch):
    chat = MagicMock()
    chat.spaces().messages().create.return_value.execute.return_value = {"name": "spaces/A/messages/1"}
    monkeypatch.setattr(api, "_require_google", lambda: (None, None, lambda *a, **k: chat))
    assert api.send({}, [], space="spaces/A", text="hi")["name"] == "spaces/A/messages/1"
    assert len(world["consents"]) == 1


def test_send_declined_is_refused_with_how_to_grant(world):
    world["declines"] = True
    with pytest.raises(SystemExit, match="majordomo login"):
        api.send({}, [], space="spaces/A", text="hi")


# --- consent_mode ---------------------------------------------------------------

def _no_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))


def test_mode_ci_is_nobody(monkeypatch):
    monkeypatch.setenv("CI", "true")
    assert api.consent_mode() is None


def test_mode_linux_display_opens_a_browser(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    assert api.consent_mode() == "browser"


def test_mode_headless_without_a_terminal_is_nobody(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _no_tty(monkeypatch)
    assert api.consent_mode() is None


def test_mode_ssh_terminal_gets_the_link(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("SSH_CONNECTION", "1 2 3 4")
    tty = types.SimpleNamespace(isatty=lambda: True)
    monkeypatch.setattr(sys, "stdin", tty)
    monkeypatch.setattr(sys, "stderr", tty)
    assert api.consent_mode() == "url"


# --- the flow keeps stdout clean ---------------------------------------------------

def test_the_flow_prints_nothing_to_stdout(monkeypatch, tmp_path, capsys):
    """Under the MCP server stdout is the protocol: the flow's own prompt
    line must land on stderr. Also checks the incremental-consent and timeout
    arguments reach the flow."""
    seen = {}

    class Flow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            seen["scopes"] = scopes
            return cls()

        def run_local_server(self, **kw):
            seen.update(kw)
            print(kw["authorization_prompt_message"].format(url="https://accounts.example/consent"))
            return types.SimpleNamespace(to_json=lambda: "{}")

    fake_flow = types.ModuleType("google_auth_oauthlib.flow")
    fake_flow.InstalledAppFlow = Flow
    fake_pkg = types.ModuleType("google_auth_oauthlib")
    fake_pkg.flow = fake_flow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", fake_pkg)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake_flow)
    client = tmp_path / "client.json"
    client.write_text("{}")
    cfg = {"api": {"client_file": str(client), "token_file": str(tmp_path / "t" / "token.json")}}
    api._consent(cfg, open_browser=False, timeout=5)
    out = capsys.readouterr()
    assert out.out == ""
    assert "https://accounts.example/consent" in out.err
    assert seen["include_granted_scopes"] == "true"
    assert seen["timeout_seconds"] == 5 and seen["open_browser"] is False
    assert seen["scopes"] == api.LOGIN_SCOPES
    assert set(roster.PEOPLE_SCOPES) <= set(api.LOGIN_SCOPES)


if __name__ == "__main__":
    _shim.run(dict(globals()))
