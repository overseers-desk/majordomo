"""majordomo without the BI cache: the public-install case.

Reading Google directly is the baseline every install can do; the cache is the
optional accelerator. So an absent database driver, or a config that names no
database, degrades to the direct read rather than stopping the program, and
only a forced --cache says what is missing.
"""

import _shim  # noqa: F401

import pytest
from typer.testing import CliRunner

from majordomo import api, config, db, readers
from majordomo.cli import app
from majordomo.errors import MajordomoError

DB = {"MYSQL_HOST": "h", "MYSQL_USER": "u", "MYSQL_PASSWORD": "p", "MYSQL_DATABASE": "d"}


def _no_driver(monkeypatch):
    monkeypatch.setattr(db, "_driver", lambda: (_ for _ in ()).throw(
        RuntimeError("the database driver is not installed; pip install 'majordomo[bi]'")))


def _driver_present(monkeypatch):
    monkeypatch.setattr(db, "_driver", lambda: (object(), object()))


def _google(monkeypatch):
    monkeypatch.setattr(api.NocacheReader, "from_config",
                        staticmethod(lambda cfg, blocked, blocked_assignees=None, **kw: "GOOGLE"))


def test_default_read_falls_back_to_google(monkeypatch):
    _no_driver(monkeypatch)
    _google(monkeypatch)
    assert readers.make_reader({"sieve": {}, "db": DB}, None) == "GOOGLE"


def test_forced_cache_names_what_to_install(monkeypatch):
    _no_driver(monkeypatch)
    with pytest.raises(MajordomoError) as ei:
        readers.make_reader({"sieve": {}, "db": DB}, "cache")
    assert "majordomo[bi]" in str(ei.value)


def test_an_absent_driver_carries_the_install_hint(monkeypatch):
    """The driver guard's message names the install option, which is what a
    forced --cache shows."""
    import sys
    monkeypatch.setitem(sys.modules, "pymysql", None)   # any import of it now fails
    with pytest.raises(RuntimeError, match=r"pip install 'majordomo\[bi\]'"):
        db._driver()


def test_a_config_without_db_falls_back_to_google(monkeypatch):
    """The driver is installed but the config names no database: the same
    unavailable cache as an absent driver, so the same fallback."""
    _driver_present(monkeypatch)
    _google(monkeypatch)
    assert readers.make_reader({"sieve": {}}, None) == "GOOGLE"


def test_connect_names_the_settings_db_lacks(monkeypatch):
    _driver_present(monkeypatch)
    with pytest.raises(MajordomoError) as ei:
        db.connect({"MYSQL_HOST": "h", "MYSQL_PORT": "3306"})
    assert str(ei.value) == ("majordomo: no cache database: [db] (or the .env beside config.toml) "
                             "lacks MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE")


def test_forced_cache_without_db_refuses_in_one_line(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text("[me]\nuser_id = 'users/1'\n")
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude-here"))
    monkeypatch.delenv("WORLD_AS_OF", raising=False)
    _driver_present(monkeypatch)
    result = CliRunner().invoke(app, ["--cache", "spaces"])
    assert result.exit_code == 1
    assert result.output.startswith("majordomo: cache unreachable with --cache (no cache database: [db] ")
    assert "lacks MYSQL_HOST" in result.output and result.output.count("majordomo: ") == 1
    assert result.output.count("\n") == 1
