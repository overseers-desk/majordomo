"""The login path must never leak credentials: Typer must not dump locals on a
crash, and ``live.login`` must turn an OAuth failure into a clean ``SystemExit``
(no raw exception, no locals). Offline — the OAuth flow is monkeypatched to fail.
"""

import _shim  # noqa: F401

import sys
import tempfile
import types

import pytest

from majordomo import live
from majordomo.cli import app


def test_typer_hides_locals():
    # A crash must not print the OAuth client secret / authorization code.
    assert app.pretty_exceptions_show_locals is False


def _raise_with_secret(*_a, **_k):
    # Mimics a rejected client: the exception carries a (placeholder) secret,
    # exactly what must not escape to the terminal.
    raise ValueError("invalid_client: secret CLIENT_SECRET_PLACEHOLDER")


def test_login_failure_is_clean_system_exit(monkeypatch):
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
        cfg = {"live": {"client_file": client_path, "token_file": f"{td}/token.json"}}
        with pytest.raises(SystemExit) as ei:
            live.login(cfg)
    msg = str(ei.value)
    assert "login failed" in msg
    assert "CLIENT_SECRET_PLACEHOLDER" not in msg


if __name__ == "__main__":  # standalone runner
    test_typer_hides_locals()
    print("ok   test_typer_hides_locals")
    print("(pytest-only: test_login_failure_is_clean_system_exit needs monkeypatch)")
