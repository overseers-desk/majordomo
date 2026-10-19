"""The login path must never leak credentials: Typer must not dump locals on a
crash, and ``frontdoor.login`` must turn an OAuth failure into a clean ``MajordomoError``
(no raw exception, no locals). Offline — the OAuth flow is monkeypatched to fail.
"""

import _shim  # noqa: F401

import sys
import tempfile
import types

import pytest

from majordomo import api, frontdoor
from majordomo.cli import app
from majordomo.errors import MajordomoError


def test_typer_hides_locals():
    # A crash must not print the OAuth client secret / authorization code.
    assert app.pretty_exceptions_show_locals is False


def _raise_with_secret(*_a, **_k):
    # Mimics a rejected client: the exception carries a (placeholder) secret,
    # exactly what must not escape to the terminal.
    raise ValueError("invalid_client: secret CLIENT_SECRET_PLACEHOLDER")


def test_login_failure_is_a_clean_refusal(monkeypatch):
    # login() imports InstalledAppFlow inside the function, so swap the module.
    flow_obj = types.SimpleNamespace(from_client_secrets_file=_raise_with_secret)
    fake_flow = types.ModuleType("google_auth_oauthlib.flow")
    fake_flow.InstalledAppFlow = flow_obj
    fake_pkg = types.ModuleType("google_auth_oauthlib")
    fake_pkg.flow = fake_flow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", fake_pkg)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake_flow)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write("{}")
        client_path = fh.name
    with tempfile.TemporaryDirectory() as td:
        cfg = {"api": {"client_file": client_path, "token_file": f"{td}/token.json"}}
        with pytest.raises(MajordomoError) as ei:
            frontdoor.login(cfg)
    msg = str(ei.value)
    assert "login failed" in msg
    assert "CLIENT_SECRET_PLACEHOLDER" not in msg


def test_get_credentials_uses_client_secret_json(monkeypatch, tmp_path):
    """client_secret.json's secret overrides the stale one baked into token.json."""
    import json

    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "token": None,
        "refresh_token": "rt_placeholder",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "id_from_token",
        "client_secret": "WRONG_SECRET",
        "scopes": ["https://www.googleapis.com/auth/chat.spaces.readonly"],
    }))

    client_file = tmp_path / "client_secret.json"
    client_file.write_text(json.dumps({
        "installed": {
            "client_id": "id_from_client_file",
            "client_secret": "RIGHT_SECRET",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }))

    cfg = {"api": {"token_file": str(token_file), "client_file": str(client_file)}}

    captured = {}

    def fake_credentials(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(valid=True)

    monkeypatch.setattr(api, "_require_google", lambda: (fake_credentials, None, None))

    creds = api.get_credentials(cfg)
    assert captured["client_secret"] == "RIGHT_SECRET", (
        f"expected RIGHT_SECRET, got {captured['client_secret']!r}"
    )
    assert captured["client_id"] == "id_from_client_file"


def _fake_flow_module(monkeypatch, run_local_server):
    flow_obj = types.SimpleNamespace(
        from_client_secrets_file=lambda *_a, **_k: types.SimpleNamespace(run_local_server=run_local_server)
    )
    fake_flow = types.ModuleType("google_auth_oauthlib.flow")
    fake_flow.InstalledAppFlow = flow_obj
    fake_pkg = types.ModuleType("google_auth_oauthlib")
    fake_pkg.flow = fake_flow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", fake_pkg)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake_flow)


def _creds():
    return types.SimpleNamespace(to_json=lambda: '{"refresh_token": "rt"}')


def test_consent_redirect_is_not_logged(monkeypatch, tmp_path, caplog):
    # google_auth_oauthlib logs the browser's redirect, authorization code and
    # all, at INFO; the MCP server's root logger shows INFO on stderr.
    import logging

    def run_local_server(**_k):
        logging.getLogger("google_auth_oauthlib.flow").info("GET /?state=s&code=AUTH_CODE_PLACEHOLDER")
        return _creds()

    _fake_flow_module(monkeypatch, run_local_server)
    client_file = tmp_path / "client_secret.json"
    client_file.write_text("{}")
    cfg = {"api": {"client_file": str(client_file), "token_file": str(tmp_path / "token.json")}}
    with caplog.at_level(logging.INFO):
        frontdoor.grant(cfg, open_browser=False)
    assert "AUTH_CODE_PLACEHOLDER" not in caplog.text
    assert logging.getLogger("google_auth_oauthlib.flow").level == logging.NOTSET


def test_token_is_created_private(monkeypatch, tmp_path):
    # Created 0600, not written world-readable and narrowed afterwards.
    import os

    _fake_flow_module(monkeypatch, lambda **_k: _creds())
    monkeypatch.setattr(os, "chmod", lambda *_a, **_k: None)
    old_umask = os.umask(0)
    try:
        client_file = tmp_path / "client_secret.json"
        client_file.write_text("{}")
        token_file = tmp_path / "token.json"
        cfg = {"api": {"client_file": str(client_file), "token_file": str(token_file)}}
        frontdoor.grant(cfg, open_browser=False)
    finally:
        os.umask(old_umask)
    assert token_file.stat().st_mode & 0o777 == 0o600


if __name__ == "__main__":  # standalone runner
    test_typer_hides_locals()
    print("ok   test_typer_hides_locals")
    print("(pytest-only: test_login_failure_is_clean_system_exit needs monkeypatch)")
