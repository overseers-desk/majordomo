"""The consent flow, which lives in the front doors: ``up_front`` asks Google
once, at the start of a command, for every scope the command's shape lacks,
when someone can answer it, then checks again; with nobody there (no display
and no terminal, CI, a WORLD_AS_OF replay) or under an identity it does not
ask. Declined or failed, the command carries on and a note gives the safe
reason. Everything the flow prints goes to stderr. ``load`` reads the user's
own files: config.toml, with the ``.env`` beside it merged into ``[db]``."""

import _shim  # noqa: F401

import errno
import sys
import types

import pytest

from majordomo import api, config, frontdoor, roster
from majordomo.errors import MajordomoError

CHAT_ONLY = ["https://www.googleapis.com/auth/chat.spaces.readonly"]
BOT = {"_identity": "bot", "identity": {"bot": {"service_account_file": "bot-key.json"}}}


def _creds(scopes):
    return types.SimpleNamespace(scopes=list(scopes), valid=True)


@pytest.fixture
def world(monkeypatch):
    """Credentials that gain the ``granted`` scopes once the consent flow
    succeeds, a recorded flow, a People build, and a display to open a
    browser on."""
    state = {"scopes": CHAT_ONLY, "granted": api.LOGIN_SCOPES, "grants": [], "declines": False}

    def grant(cfg, *, open_browser=True, timeout=None):
        state["grants"].append({"open_browser": open_browser, "timeout": timeout})
        if state["declines"]:
            raise MajordomoError("majordomo: login failed or was not completed (AccessDeniedError).")
        state["scopes"] = state["granted"]
        return "token.json"

    monkeypatch.setattr(api, "get_credentials", lambda cfg: _creds(state["scopes"]))
    monkeypatch.setattr(api, "_require_google", lambda: (None, None, lambda *a, **k: "PEOPLE"))
    monkeypatch.setattr(frontdoor, "grant", grant)
    monkeypatch.setattr(frontdoor, "consent_mode", lambda: "browser")
    config.drain_notes()
    return state


def test_one_grant_covers_every_missing_group_and_the_command_carries_on(world):
    cfg = {}
    frontdoor.up_front(cfg, api_read=True, owner=True, people=True)
    assert world["grants"] == [{"open_browser": True, "timeout": frontdoor.CONSENT_TIMEOUT}]
    assert api.people_service(cfg) == "PEOPLE"
    assert len(world["grants"]) == 1
    assert config.drain_notes() == []


def test_a_declined_grant_is_not_asked_again_in_the_same_command(world):
    world["declines"] = True
    cfg = {}
    frontdoor.up_front(cfg, people=True)
    assert api.people_service(cfg) is None
    assert len(world["grants"]) == 1
    assert roster.LOGIN_NOTE in config.drain_notes()
    frontdoor.up_front({}, people=True)  # the next command asks again
    assert len(world["grants"]) == 2


def test_a_grant_short_of_a_scope_is_checked_again_once_and_noted(world):
    world["granted"] = CHAT_ONLY + roster.PEOPLE_SCOPES  # the person unticked one
    frontdoor.up_front({}, owner=True, people=True)
    assert len(world["grants"]) == 1
    notes = config.drain_notes()
    assert len(notes) == 1 and "listing space members, for --owner" in notes[0]


def test_nobody_to_answer_means_no_grant_and_no_refusal(world, monkeypatch):
    monkeypatch.setattr(frontdoor, "consent_mode", lambda: None)
    frontdoor.up_front({}, people=True)
    assert world["grants"] == []
    assert config.drain_notes() == []


def test_world_as_of_never_grants(world):
    frontdoor.up_front(config.load_config({}, world_as_of="2026-01-01T00:00:00+00:00"), people=True)
    assert world["grants"] == []


def test_an_identity_is_never_asked(world):
    for shape in ({"send": True}, {"api_read": True, "people": True, "own": True},
                  {"api_read": True, "owner": True, "people": True}):
        frontdoor.up_front(BOT, **shape)
    assert world["grants"] == []
    assert config.drain_notes() == []


def test_the_consent_line_names_the_purpose_on_stderr(world, capsys):
    frontdoor.up_front({}, people=True)
    out = capsys.readouterr()
    assert out.out == ""
    assert "(the People API, which names people); opening Google's consent page" in out.err


def test_a_terminal_without_a_display_gets_the_link_not_a_browser(world, monkeypatch, capsys):
    monkeypatch.setattr(frontdoor, "consent_mode", lambda: "url")
    frontdoor.up_front({}, people=True)
    assert world["grants"][0]["open_browser"] is False
    assert f"forward port {frontdoor.CONSENT_PORT}" in capsys.readouterr().err


# --- the flow itself, over a stand-in for google_auth_oauthlib ---------------

def _flow(monkeypatch, run_local_server):
    seen = {}

    class Flow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            seen["scopes"] = scopes
            return cls()

        def run_local_server(self, **kw):
            seen.update(kw)
            return run_local_server(**kw)

    fake_flow = types.ModuleType("google_auth_oauthlib.flow")
    fake_flow.InstalledAppFlow = Flow
    fake_pkg = types.ModuleType("google_auth_oauthlib")
    fake_pkg.flow = fake_flow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", fake_pkg)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake_flow)
    return seen


def _client_cfg(tmp_path):
    client = tmp_path / "client.json"
    client.write_text("{}")
    return {"api": {"client_file": str(client), "token_file": str(tmp_path / "t" / "token.json")}}


def test_the_flow_prints_nothing_to_stdout(monkeypatch, tmp_path, capsys):
    """Under the MCP server stdout is the protocol: the flow's own prompt
    line must land on stderr. Also checks the incremental-consent and timeout
    arguments reach the flow."""
    def run(**kw):
        print(kw["authorization_prompt_message"].format(url="https://accounts.example/consent"))
        return types.SimpleNamespace(to_json=lambda: "{}")

    seen = _flow(monkeypatch, run)
    frontdoor.grant(_client_cfg(tmp_path), open_browser=False, timeout=5)
    out = capsys.readouterr()
    assert out.out == ""
    assert "https://accounts.example/consent" in out.err
    assert seen["include_granted_scopes"] == "true"
    assert seen["timeout_seconds"] == 5 and seen["open_browser"] is False
    assert seen["scopes"] == api.LOGIN_SCOPES
    assert set(roster.PEOPLE_SCOPES) <= set(api.LOGIN_SCOPES)


def test_a_decline_leaves_a_safe_reason_and_no_refusal(monkeypatch, tmp_path, capsys):
    class AccessDeniedError(Exception):
        pass

    def run(**kw):
        raise AccessDeniedError("access_denied code=AUTH_CODE_PLACEHOLDER secret=CLIENT_SECRET_PLACEHOLDER")

    _flow(monkeypatch, run)
    monkeypatch.setattr(api, "get_credentials", lambda cfg: _creds(CHAT_ONLY))
    monkeypatch.setattr(frontdoor, "consent_mode", lambda: "browser")
    config.drain_notes()
    frontdoor.up_front(_client_cfg(tmp_path), people=True)
    notes = config.drain_notes()
    assert len(notes) == 1
    assert "login failed or was not completed (AccessDeniedError (consent declined / access_denied))" in notes[0]
    assert "PLACEHOLDER" not in notes[0]
    out = capsys.readouterr()
    assert out.out == "" and "PLACEHOLDER" not in out.err


# --- consent_mode ---------------------------------------------------------------

def _no_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))


def test_mode_ci_is_nobody(monkeypatch):
    monkeypatch.setenv("CI", "true")
    assert frontdoor.consent_mode() is None


def test_mode_linux_display_opens_a_browser(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    assert frontdoor.consent_mode() == "browser"


def test_mode_headless_without_a_terminal_is_nobody(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _no_tty(monkeypatch)
    assert frontdoor.consent_mode() is None


def test_mode_ssh_terminal_gets_the_link(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("SSH_CONNECTION", "1 2 3 4")
    tty = types.SimpleNamespace(isatty=lambda: True)
    monkeypatch.setattr(sys, "stdin", tty)
    monkeypatch.setattr(sys, "stderr", tty)
    assert frontdoor.consent_mode() == "url"


# --- the failure reason -----------------------------------------------------

def test_consent_reason_is_the_class_name_for_an_unrecognised_exception():
    assert frontdoor._consent_reason(ValueError("CLIENT_SECRET_PLACEHOLDER")) == "ValueError"


def test_consent_reason_never_carries_the_exception_s_own_message():
    reason = frontdoor._consent_reason(ValueError("secret=abc123 code=xyz"))
    assert "secret" not in reason and "abc123" not in reason and "xyz" not in reason


def test_consent_reason_known_cases_get_a_plain_sentence():
    class WSGITimeoutError(Exception):
        pass

    assert frontdoor._consent_reason(WSGITimeoutError("irrelevant")) == (
        "WSGITimeoutError (timed out waiting for approval)"
    )

    port_in_use = OSError("Address already in use")
    port_in_use.errno = errno.EADDRINUSE
    assert frontdoor._consent_reason(port_in_use) == f"OSError (port {frontdoor.CONSENT_PORT} already in use)"

    class AccessDeniedError(Exception):
        pass

    assert frontdoor._consent_reason(AccessDeniedError("access_denied")) == (
        "AccessDeniedError (consent declined / access_denied)"
    )

    other_os_error = OSError("permission denied")
    other_os_error.errno = errno.EACCES
    assert frontdoor._consent_reason(other_os_error) == "OSError"


# --- login ------------------------------------------------------------------


def test_login_runs_the_flow_and_waits_for_the_person(monkeypatch):
    seen = {}

    def grant(cfg, **kw):
        seen.update(kw)
        return "token.json"

    monkeypatch.setattr(frontdoor, "grant", grant)
    assert frontdoor.login({}) == "token.json"
    assert seen == {}


def test_identity_has_no_login(monkeypatch):
    monkeypatch.setattr(frontdoor, "grant", lambda cfg, **kw: pytest.fail("granted"))
    with pytest.raises(MajordomoError) as ei:
        frontdoor.login(BOT)
    assert "there is no login" in str(ei.value)


# --- load -------------------------------------------------------------------


def _home_config(tmp_path, monkeypatch, toml):
    (tmp_path / "config.toml").write_text(toml)
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.delenv("WORLD_AS_OF", raising=False)


def test_load_merges_the_env_file_into_db_and_db_keys_win(tmp_path, monkeypatch):
    _home_config(tmp_path, monkeypatch, "[db]\nMYSQL_HOST = 'from-toml'\n")
    (tmp_path / ".env").write_text("# the cache\nMYSQL_HOST=from-env\nMYSQL_USER='u'\n")
    assert frontdoor.load()["db"] == {"MYSQL_HOST": "from-toml", "MYSQL_USER": "u"}


def test_load_without_an_env_file_adds_no_db(tmp_path, monkeypatch):
    _home_config(tmp_path, monkeypatch, "[me]\nuser_id = 'users/1'\n")
    assert "db" not in frontdoor.load()


if __name__ == "__main__":
    _shim.run(dict(globals()))
