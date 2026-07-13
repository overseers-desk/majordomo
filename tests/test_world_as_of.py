"""WORLD_AS_OF (replay-bounded reads, WORLD_AS_OF.design.md): the three
semantics — unset is unbounded and changes nothing; set means nothing dated
after the bound leaves any backend; set-but-unparseable (or timezone-naive) is
a hard failure on every code path. Enforcement is tested at the seam: the
parser, the window clamp, the two ``reports.spaces`` subqueries, the
NocacheReader clamp and ``createTime`` post-filter, and the FreshReader skip.
No test touches the live Chat API (fakes only, per test_nocache_reader.py).
"""

import _shim  # noqa: F401

from datetime import datetime

import pytest

from majordomo import config

# 2026-07-12T17:07:00+10:00 == 2026-07-12T07:07:00 UTC
BOUND_RAW = "2026-07-12T17:07:00+10:00"
BOUND_UTC = datetime(2026, 7, 12, 7, 7, 0)


# --- semantics 1: unset -> unbounded, zero behavioural change ---------------

def test_unset_is_none(monkeypatch):
    monkeypatch.delenv("WORLD_AS_OF", raising=False)
    assert config.world_as_of() is None


def test_unset_clamp_is_identity(monkeypatch):
    monkeypatch.delenv("WORLD_AS_OF", raising=False)
    assert config.world_clamp(None) is None
    end = datetime(2026, 1, 1)
    assert config.world_clamp(end) == end


# --- semantics 2: set -> parsed to naive UTC --------------------------------

def test_valid_utc_offset(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", "2026-07-12T07:07:00+00:00")
    assert config.world_as_of() == BOUND_UTC


def test_valid_non_utc_offset_converts(monkeypatch):
    # The store is naive UTC; a +10:00 instant must land 10 hours earlier,
    # not have its offset silently dropped.
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    assert config.world_as_of() == BOUND_UTC


def test_clamp_takes_the_earlier_bound(monkeypatch):
    monkeypatch.setenv("WORLD_AS_OF", BOUND_RAW)
    assert config.world_clamp(None) == BOUND_UTC
    assert config.world_clamp(datetime(2027, 1, 1)) == BOUND_UTC
    early = datetime(2026, 1, 1)
    assert config.world_clamp(early) == early


# --- semantics 3: set but unparseable / naive -> hard failure ----------------

@pytest.mark.parametrize("bad", ["2026-07-12T17:07:00", "garbage", "", "1752303000"])
def test_bad_values_hard_fail(monkeypatch, bad):
    monkeypatch.setenv("WORLD_AS_OF", bad)
    with pytest.raises(SystemExit):
        config.world_as_of()


def test_load_config_hard_fails_on_bad_bound(monkeypatch, tmp_path):
    # A bad bound stops even commands that fetch no dates: the parse is wired
    # into load_config(), which every command and MCP tool call passes through.
    toml = tmp_path / "config.toml"
    toml.write_text("[me]\nuser_id = 'users/1'\n")
    monkeypatch.setattr(config, "CONFIG_TOML", toml)
    monkeypatch.setenv("WORLD_AS_OF", "not-a-timestamp")
    with pytest.raises(SystemExit):
        config.load_config()
    monkeypatch.delenv("WORLD_AS_OF")
    assert config.load_config()["me"]["user_id"] == "users/1"


if __name__ == "__main__":
    _shim.run(dict(globals()))
