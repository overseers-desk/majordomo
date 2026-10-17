"""load_config's sources: a dict, taken as it stands and never changed;
a TOML file, whose [api] files default to beside it and whose paths expand
``~``; and nothing, the user's own config.toml. Every source is checked for
the shape majordomo reads."""

import _shim  # noqa: F401

import copy
import os
import re
from pathlib import Path

import pytest

from majordomo import api, config, readers
from majordomo.errors import MajordomoError


def test_a_dict_is_never_changed_and_is_taken_literally():
    given = {"identity": {"bot": {"service_account_file": "~/key.json"}},
             "sieve": {"block_spaces": ["spaces/A"]}, "extra": {"kept": True}}
    before = copy.deepcopy(given)
    cfg = config.load_config(given, identity="bot")
    cfg["sieve"]["block_spaces"].append("spaces/B")
    assert given == before
    assert cfg["_identity"] == "bot" and cfg["extra"] == {"kept": True}
    assert "api" not in cfg
    assert cfg["identity"]["bot"]["service_account_file"] == "~/key.json"


def test_a_file_gets_its_api_files_beside_it_and_tilde_expanded(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[api]\ntoken_file = '~/t.json'\n[identity.bot]\nservice_account_file = '~/key.json'\n")
    cfg = config.load_config(str(toml))
    assert cfg["api"] == {"token_file": os.path.expanduser("~/t.json"),
                          "client_file": str(tmp_path / "client_secret.json")}
    assert cfg["identity"]["bot"]["service_account_file"] == os.path.expanduser("~/key.json")
    assert config.load_config(toml) == cfg


def test_no_source_reads_the_user_s_config_toml(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / "config.toml").write_text("[me]\nuser_id = 'users/1'\n")
    cfg = config.load_config()
    assert cfg["me"] == {"user_id": "users/1"}
    assert cfg["api"]["token_file"] == str(tmp_path / "token.json")


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(MajordomoError, match=re.escape(f"majordomo: no config at {tmp_path / 'absent.toml'}")):
        config.load_config(tmp_path / "absent.toml")


@pytest.mark.parametrize("given, where", [
    ({"sieve": ["spaces/A"]}, "[sieve]"),
    ({"sieve": {"block_spaces": "spaces/A"}}, "[sieve].block_spaces"),
    ({"sieve": {"block_assignees": [1]}}, "[sieve].block_assignees"),
    ({"db": "mysql://h"}, "[db]"),
    ({"identity": {"bot": "key.json"}}, "[identity.bot]"),
])
def test_a_config_of_the_wrong_shape_is_refused(given, where):
    with pytest.raises(MajordomoError, match=re.escape(where)):
        config.load_config(given)


def test_a_dict_without_api_or_credentials_reads_nothing_from_home(monkeypatch):
    api._require_google()  # imported before the home directory is fenced off

    def home(*args, **kwargs):
        raise AssertionError("read the home directory")

    monkeypatch.setattr(config, "config_dir", home)
    monkeypatch.setattr(os.path, "expanduser", home)
    monkeypatch.setattr(Path, "home", home)
    cfg = config.load_config({"sieve": {}})
    with pytest.raises(MajordomoError, match=re.escape("[api].token_file")):
        readers.make_reader(cfg, "nocache")


if __name__ == "__main__":
    _shim.run(dict(globals()))
