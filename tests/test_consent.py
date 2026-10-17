"""A token short of a scope a command needs, as the core sees it: the core
never asks Google for anything. ``ensure_command_scopes`` raises ScopeError
for the union of what a command's shape lacks, and stays quiet when there is
nothing to ask (an empty shape, no saved token, a WORLD_AS_OF replay, a Chat
app identity); a
feature short of its own scope falls back softly (a name shown as an id) or
refuses (send). Asking Google, when someone can answer, is the front door's
(test_frontdoor.py)."""

import _shim  # noqa: F401

import types
from unittest.mock import MagicMock

import pytest

from majordomo import api, config, frontdoor, roster
from majordomo.errors import MajordomoError, ScopeError

CHAT_ONLY = ["https://www.googleapis.com/auth/chat.spaces.readonly"]


def _creds(scopes):
    return types.SimpleNamespace(scopes=list(scopes), valid=True)


@pytest.fixture
def world(monkeypatch):
    """Credentials holding only a Chat read scope, a People build, and a
    consent flow that fails the test if the core ever reaches for it."""
    state = {"scopes": CHAT_ONLY}

    def grant(cfg, **kw):
        raise AssertionError("the core ran the consent flow")

    monkeypatch.setattr(api, "get_credentials", lambda cfg: _creds(state["scopes"]))
    monkeypatch.setattr(api, "_require_google", lambda: (None, None, lambda *a, **k: "PEOPLE"))
    monkeypatch.setattr(frontdoor, "grant", grant)
    monkeypatch.setattr(frontdoor, "consent_mode", lambda: "browser")
    config.drain_notes()
    return state


def test_a_token_short_of_the_people_scope_names_nobody_and_says_how_to_grant(world):
    assert api.people_service({}) is None
    assert config.drain_notes() == [roster.LOGIN_NOTE]


def test_a_token_that_has_the_scopes_builds_the_service(world):
    world["scopes"] = api.LOGIN_SCOPES
    assert api.people_service({}) == "PEOPLE"
    assert config.drain_notes() == []


def test_no_saved_token_names_nobody_and_says_to_log_in(world, monkeypatch):
    def no_token(cfg):
        raise MajordomoError("majordomo: no OAuth token at ...; run `majordomo login`.")

    monkeypatch.setattr(api, "get_credentials", no_token)
    assert api.people_service({}) is None
    assert config.drain_notes() == [roster.NO_LOGIN_NOTE]


def test_own_tasks_short_of_its_scope_says_so(world):
    assert api.own_tasks({}) is None
    assert config.drain_notes() == [api.OWN_NOTE]


def test_send_short_of_its_scope_raises_naming_it(world, monkeypatch):
    chat = MagicMock()
    monkeypatch.setattr(api, "_require_google", lambda: (None, None, lambda *a, **k: chat))
    with pytest.raises(ScopeError, match="majordomo login") as ei:
        api.send({}, space="spaces/A", text="hi")
    assert ei.value.scopes == [api.SEND_SCOPE]
    assert ei.value.purpose == "sending messages"
    chat.spaces().messages().create.assert_not_called()


# --- scopes_for / ensure_command_scopes: the up-front, per-command check -----
#
# A command works out its own shape and learns everything that shape lacks in
# one go, before any Chat or People call.

def test_scopes_for_maps_a_shape_to_its_scopes():
    assert api.scopes_for() == []
    assert api.scopes_for(send=True) == [api.SEND_SCOPE]
    assert api.scopes_for(owner=True) == [api.MEMBERSHIPS_SCOPE]
    assert api.scopes_for(people=True) == roster.PEOPLE_SCOPES
    assert api.scopes_for(api_read=True) == api.CHAT_READ_SCOPES
    assert api.scopes_for(api_read=True, owner=True, people=True) == (
        api.CHAT_READ_SCOPES + [api.MEMBERSHIPS_SCOPE] + roster.PEOPLE_SCOPES
    )


def test_ensure_command_scopes_raises_the_union_of_what_is_missing(world):
    with pytest.raises(ScopeError, match="majordomo login") as ei:
        api.ensure_command_scopes({}, api_read=True, owner=True, people=True)
    unread = [s for s in api.CHAT_READ_SCOPES if s not in CHAT_ONLY]
    assert ei.value.scopes == [*unread, api.MEMBERSHIPS_SCOPE, *roster.PEOPLE_SCOPES]
    assert ei.value.purpose == (
        "reading Chat directly; listing space members, for --owner; the People API, which names people"
    )


def test_ensure_command_scopes_is_quiet_for_an_empty_shape(world):
    api.ensure_command_scopes({})


def test_ensure_command_scopes_is_quiet_when_everything_is_granted(world):
    world["scopes"] = api.LOGIN_SCOPES
    api.ensure_command_scopes({}, api_read=True, owner=True, send=True, people=True, own=True)


def test_ensure_command_scopes_is_quiet_with_no_saved_token_at_all(world, monkeypatch):
    def no_token(cfg):
        raise MajordomoError("majordomo: no OAuth token at ...; run `majordomo login`.")

    monkeypatch.setattr(api, "get_credentials", no_token)
    api.ensure_command_scopes({}, people=True)


def test_ensure_command_scopes_is_quiet_under_world_as_of(world):
    api.ensure_command_scopes(config.load_config({}, world_as_of="2026-01-01T00:00:00+00:00"), people=True)


BOT = {"_identity": "bot", "identity": {"bot": {"service_account_file": "bot-key.json"}}}


def test_identity_asks_nothing_for_every_shape(monkeypatch):
    def touched(cfg):
        raise AssertionError("the personal login was touched")

    monkeypatch.setattr(api, "get_credentials", touched)
    for flags in ({}, {"send": True}, {"api_read": True}, {"people": True}, {"own": True},
                  {"api_read": True, "owner": True, "people": True}, {"send": True, "people": True}):
        assert api.ensure_command_scopes(BOT, **flags) is None, flags


if __name__ == "__main__":
    _shim.run(dict(globals()))
