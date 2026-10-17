"""Config accessors: ``[me]`` (email cannot drive filters in v1, so the CLI
keys off ``[me].user_id``), the sieve, and ``[identity.NAME]``, the Chat app a
command sends as with ``--as NAME``.
"""

import _shim  # noqa: F401

import pytest

from majordomo import config
from majordomo.errors import MajordomoError


def _config_toml(tmp_path, monkeypatch, text):
    toml = tmp_path / "config.toml"
    toml.write_text(text)
    monkeypatch.delenv("WORLD_AS_OF", raising=False)
    return toml


def test_user_id_present():
    assert config.me_user_id({"me": {"user_id": "users/123"}}) == "users/123"


def test_user_id_absent_even_with_email():
    assert config.me_user_id({"me": {"google_id": "you@example.com"}}) is None
    assert config.me_user_id({}) is None


def test_google_id_is_kept_for_later():
    assert config.me_google_id({"me": {"google_id": "you@example.com"}}) == "you@example.com"


def test_block_spaces():
    assert config.block_spaces({"sieve": {"block_spaces": ["spaces/A", "spaces/B"]}}) == ["spaces/A", "spaces/B"]
    assert config.block_spaces({}) == []


def test_load_config_records_the_named_identity(tmp_path, monkeypatch):
    toml = _config_toml(tmp_path, monkeypatch, "[identity.bot]\nservice_account_file = '/keys/bot-key.json'\n")
    cfg = config.load_config(toml, identity="bot")
    assert config.identity(cfg) == {"name": "bot", "service_account_file": "/keys/bot-key.json"}


def test_without_a_name_the_command_acts_as_the_personal_login(tmp_path, monkeypatch):
    toml = _config_toml(tmp_path, monkeypatch, "[identity.bot]\nservice_account_file = '/keys/bot-key.json'\n")
    assert config.identity(config.load_config(toml)) is None


def test_an_unknown_identity_stops_the_command(tmp_path, monkeypatch):
    toml = _config_toml(tmp_path, monkeypatch, "[identity.bot]\nservice_account_file = '/keys/bot-key.json'\n")
    with pytest.raises(MajordomoError) as ei:
        config.load_config(toml, identity="nosuch")
    assert "[identity.nosuch]" in str(ei.value)
    assert "(configured: bot)" in str(ei.value)


def test_an_identity_without_a_key_file_stops_the_command(tmp_path, monkeypatch):
    toml = _config_toml(tmp_path, monkeypatch, "[identity.bot]\n")
    with pytest.raises(MajordomoError) as ei:
        config.load_config(toml, identity="bot")
    assert "service_account_file" in str(ei.value)


if __name__ == "__main__":
    _shim.run(dict(globals()))
