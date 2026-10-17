"""majordomo without the BI cache driver: the public-install case.

Reading Google directly is the baseline every install can do; the cache is the
optional accelerator. So an absent database driver degrades to the direct read
rather than stopping the program, and only a forced --cache says what to install.
"""

import _shim  # noqa: F401

import pytest

from majordomo import api, db, readers


def _no_driver(monkeypatch):
    monkeypatch.setattr(db, "_driver", lambda: (_ for _ in ()).throw(
        RuntimeError("the database driver is not installed; pip install 'majordomo[bi]'")))


def test_default_read_falls_back_to_google(monkeypatch):
    _no_driver(monkeypatch)
    monkeypatch.setattr(api.NocacheReader, "from_config",
                        staticmethod(lambda cfg, blocked, blocked_assignees=None: "GOOGLE"))
    assert readers.make_reader({"sieve": {}}, None) == "GOOGLE"


def test_forced_cache_names_what_to_install(monkeypatch):
    _no_driver(monkeypatch)
    with pytest.raises(SystemExit) as ei:
        readers.make_reader({"sieve": {}}, "cache")
    assert "majordomo[bi]" in str(ei.value)


def test_driver_absence_is_an_ordinary_exception(monkeypatch):
    """SystemExit here would escape the reader seam's `except Exception` and
    kill the fallback, so the driver guard raises something catchable."""
    import sys
    monkeypatch.setitem(sys.modules, "pymysql", None)   # any import of it now fails
    try:
        db._driver()
    except Exception as exc:                            # the seam catches this
        assert "majordomo[bi]" in str(exc)
    else:
        raise AssertionError("a missing driver raised nothing")


def _no_env_file(monkeypatch, tmp_path):
    from majordomo import config
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "absent-env")   # never created
    monkeypatch.setattr(db, "_driver", lambda: (object(), object()))   # driver present


def test_missing_env_file_falls_back_to_google(monkeypatch, tmp_path):
    """The driver is installed but the database credentials file is absent: the
    same unavailable cache as an absent driver, so the same fallback."""
    _no_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(api.NocacheReader, "from_config",
                        staticmethod(lambda cfg, blocked, blocked_assignees=None: "GOOGLE"))
    assert readers.make_reader({"sieve": {}}, None) == "GOOGLE"


def test_forced_cache_with_missing_env_file_exits_with_one_line(monkeypatch, tmp_path):
    _no_env_file(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as ei:
        readers.make_reader({"sieve": {}}, "cache")
    msg = str(ei.value)
    assert "no database .env at" in msg and "--cache" in msg and "\n" not in msg
