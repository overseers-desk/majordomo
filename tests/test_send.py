"""api.send behaviour with an injected fake Chat service (no creds/network):
the create-call shape, thread derivation from a message name, and the
refusals: blocked space (worded as not-found), a 404 (same wording), a set
WORLD_AS_OF, a token without the send scope, and a missing target.
"""

import _shim  # noqa: F401

import types
from unittest.mock import MagicMock

import pytest

from majordomo import api


def _chat(created=None):
    chat = MagicMock()
    chat.spaces().messages().create.return_value.execute.return_value = created or {}
    return chat


def _create_kwargs(chat):
    _, kwargs = chat.spaces().messages().create.call_args
    return kwargs


def test_send_to_space_calls_create():
    chat = _chat({"name": "spaces/OK/messages/NEW"})
    out = api.send({}, [], space="spaces/OK", text="hi", service=chat)
    assert out["name"] == "spaces/OK/messages/NEW"
    kwargs = _create_kwargs(chat)
    assert kwargs["parent"] == "spaces/OK"
    assert kwargs["body"] == {"text": "hi"}
    assert "messageReplyOption" not in kwargs


def test_reply_derives_thread_from_message_name():
    chat = _chat()
    api.send({}, [], thread="spaces/OK/messages/T.2", text="re", service=chat)
    kwargs = _create_kwargs(chat)
    assert kwargs["parent"] == "spaces/OK"
    assert kwargs["body"]["thread"] == {"name": "spaces/OK/threads/T"}
    assert kwargs["messageReplyOption"] == "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"


def test_reply_accepts_thread_resource_name():
    chat = _chat()
    api.send({}, [], thread="spaces/OK/threads/T", text="re", service=chat)
    assert _create_kwargs(chat)["body"]["thread"] == {"name": "spaces/OK/threads/T"}


def test_blocked_space_worded_as_not_found():
    chat = _chat()
    with pytest.raises(SystemExit) as ei:
        api.send({}, ["spaces/BLOCK"], space="spaces/BLOCK", text="hi", service=chat)
    assert str(ei.value) == "majordomo: spaces/BLOCK: not found."
    chat.spaces().messages().create.assert_not_called()


def test_blocked_space_refused_for_a_thread_reply_too():
    chat = _chat()
    with pytest.raises(SystemExit) as ei:
        api.send({}, ["spaces/BLOCK"], thread="spaces/BLOCK/messages/T.1", text="hi", service=chat)
    assert str(ei.value) == "majordomo: spaces/BLOCK: not found."
    chat.spaces().messages().create.assert_not_called()


def test_404_uses_the_same_wording_as_the_sieve():
    chat = MagicMock()
    err = Exception("404")
    err.resp = types.SimpleNamespace(status=404)
    chat.spaces().messages().create.return_value.execute.side_effect = err
    with pytest.raises(SystemExit) as ei:
        api.send({}, [], space="spaces/GONE", text="hi", service=chat)
    assert str(ei.value) == "majordomo: spaces/GONE: not found."


def test_non_404_api_errors_stay_loud():
    chat = MagicMock()
    err = Exception("403")
    err.resp = types.SimpleNamespace(status=403)
    chat.spaces().messages().create.return_value.execute.side_effect = err
    with pytest.raises(Exception, match="403"):
        api.send({}, [], space="spaces/OK", text="hi", service=chat)


def test_world_as_of_refuses_send(monkeypatch):
    monkeypatch.setenv(api.config.WORLD_AS_OF_ENV, "2026-07-12T17:07:00+10:00")
    chat = _chat()
    with pytest.raises(SystemExit) as ei:
        api.send({}, [], space="spaces/OK", text="hi", service=chat)
    assert "WORLD_AS_OF" in str(ei.value)
    chat.spaces().messages().create.assert_not_called()


def test_token_without_send_scope_points_at_login(monkeypatch):
    read_only = types.SimpleNamespace(
        scopes=["https://www.googleapis.com/auth/chat.spaces.readonly"])
    monkeypatch.setattr(api, "get_credentials", lambda cfg: read_only)
    with pytest.raises(SystemExit) as ei:
        api.send({}, [], space="spaces/OK", text="hi")
    assert "majordomo login" in str(ei.value)


def test_needs_exactly_one_target():
    with pytest.raises(SystemExit):
        api.send({}, [], text="hi", service=_chat())
    with pytest.raises(SystemExit):
        api.send({}, [], space="spaces/OK", thread="spaces/OK/threads/T",
                 text="hi", service=_chat())


def test_login_mints_the_send_scope():
    assert api.SEND_SCOPE in api.LOGIN_SCOPES
