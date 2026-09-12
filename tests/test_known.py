"""known.py: the state file's round trip and atomic write, mention parsing,
person and space resolution (id, email, whole name, substring, ambiguity,
none, a backend seed), the XDG path, and WORLD_AS_OF read-only with the
first-seen cut."""

import _shim  # noqa: F401

import json
from datetime import datetime
from pathlib import Path

import pytest

from majordomo import config, known


def _known(tmp_path) -> known.Known:
    return known.Known(tmp_path / "known.tsv")


# --- the file ---------------------------------------------------------------

def test_round_trip_and_last_seen_moves(tmp_path):
    k = _known(tmp_path)
    k.remember("users/1", known.NAME, "Alice", datetime(2026, 1, 1), "mention")
    k.remember("users/1", known.NAME, "Alice", datetime(2026, 3, 1), "mention")
    k.remember("users/1", known.NAME, "Alice", datetime(2025, 12, 1), "task")
    k.save()
    again = _known(tmp_path)
    assert again.facts("users/1", known.NAME) == [("Alice", "2025-12-01T00:00:00Z", "2026-03-01T00:00:00Z")]
    header = (tmp_path / "known.tsv").read_text().splitlines()[0]
    assert header == "\t".join(known.FIELDS)


def test_save_is_atomic_and_leaves_no_temp(tmp_path):
    k = _known(tmp_path)
    k.remember("spaces/A", known.NAME, "Kitchen Ops", None, "cache")
    k.save()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["known.tsv"]


def test_nothing_learnt_writes_nothing(tmp_path):
    k = _known(tmp_path)
    k.save()
    assert not (tmp_path / "known.tsv").exists()


def test_default_path_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert known.Known().path == tmp_path / "majordomo" / "known.tsv"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert known.Known().path == Path.home() / ".local" / "state" / "majordomo" / "known.tsv"


# --- mentions ---------------------------------------------------------------

ANN = [{"type": "USER_MENTION", "startIndex": 0, "length": 8,
        "userMention": {"user": {"name": "users/1", "type": "HUMAN"}, "type": "MENTION"}},
       {"type": "USER_MENTION", "startIndex": 22, "length": 4,
        "userMention": {"user": {"name": "users/2"}}}]
TEXT = "@Alice B can you ping @Bob"


def test_mentions_from_list_and_json():
    assert known.mentions_of(TEXT, ANN) == [("users/1", "Alice B"), ("users/2", "Bob")]
    assert known.mentions_of(TEXT, json.dumps(ANN)) == [("users/1", "Alice B"), ("users/2", "Bob")]


def test_mentions_skip_offsets_without_an_at():
    assert known.mentions_of("plain text", ANN) == []
    assert known.mentions_of(TEXT, "not json") == []
    assert known.mentions_of(None, ANN) == []


# --- resolving people -------------------------------------------------------

def _seeded(tmp_path) -> known.Known:
    k = _known(tmp_path)
    k.remember("users/1", known.NAME, "Alice Smith", datetime(2025, 7, 1), "mention")
    k.remember("users/2", known.NAME, "Alice S", datetime(2025, 6, 1), "task")
    k.remember("users/2", known.NAME, "Alice S DS", datetime(2025, 9, 15), "mention")
    k.remember("users/3", known.NAME, "Bob Builder", datetime(2025, 8, 1), "mention")
    k.remember("users/3", known.EMAIL, "bob@example.com", None, "api")
    k.remember("users/3", known.DM_SPACE, "spaces/DM3", None, "cache")
    return k


def test_id_passes_through(tmp_path):
    assert _seeded(tmp_path).resolve_person("users/99") == "users/99"


def test_whole_name_beats_substring(tmp_path):
    assert _seeded(tmp_path).resolve_person("alice s") == "users/2"


def test_substring_unique(tmp_path):
    assert _seeded(tmp_path).resolve_person("bob") == "users/3"
    assert _seeded(tmp_path).resolve_person("@Bob") == "users/3"


def test_ambiguous_lists_candidates(tmp_path):
    with pytest.raises(SystemExit) as exc:
        _seeded(tmp_path).resolve_person("Alice")
    msg = str(exc.value)
    assert "matches 2 people" in msg
    assert "users/1 (Alice Smith)" in msg
    assert "users/2 (Alice S DS, Alice S)" in msg


def test_unknown_name_fails_then_seed_helps(tmp_path):
    k = _seeded(tmp_path)
    with pytest.raises(SystemExit, match="no one seen as 'Carol'"):
        k.resolve_person("Carol")
    calls = []

    def seed():
        calls.append(1)
        k.remember("users/4", known.NAME, "Carol Danvers", datetime(2026, 1, 1), "mention")

    assert k.resolve_person("Carol", seed=seed) == "users/4"
    assert calls == [1]


def test_email_from_file_then_backend(tmp_path):
    k = _seeded(tmp_path)
    assert k.resolve_person("BOB@example.com") == "users/3"
    with pytest.raises(SystemExit, match="no one known by"):
        k.resolve_person("new@example.com")
    assert k.resolve_person("new@example.com", by_email=lambda e: "users/5") == "users/5"


def test_lookups(tmp_path):
    k = _seeded(tmp_path)
    assert k.names_of("users/2") == ["Alice S DS", "Alice S"]
    assert k.email_of("users/3") == "bob@example.com"
    assert k.dm_space_of("users/3") == "spaces/DM3"
    assert k.dm_space_of("users/1") is None


# --- resolving spaces -------------------------------------------------------

def test_space_by_name_and_seed(tmp_path):
    k = _known(tmp_path)
    assert k.resolve_space("spaces/X") == "spaces/X"
    k.remember("spaces/A", known.NAME, "Back Office (Digital)", None, "cache")
    k.remember("spaces/B", known.NAME, "Marketing + PR", None, "cache")
    assert k.resolve_space("back office") == "spaces/A"
    with pytest.raises(SystemExit, match="no space named 'Kitchen'"):
        k.resolve_space("Kitchen")
    seeded = []
    assert k.resolve_space("Kitchen", seed=lambda: (k.remember("spaces/C", known.NAME, "Kitchen", None, "api"), seeded.append(1))) == "spaces/C"
    k2 = _known(tmp_path)
    k2.remember("spaces/A", known.NAME, "Marketing", None, "cache")
    k2.remember("spaces/B", known.NAME, "Marketing + PR", None, "cache")
    assert k2.resolve_space("marketing") == "spaces/A"
    with pytest.raises(SystemExit, match="matches 2 spaces"):
        k2.resolve_space("market")


# --- WORLD_AS_OF ------------------------------------------------------------

def test_bounded_run_never_writes(tmp_path, monkeypatch):
    monkeypatch.setenv(config.WORLD_AS_OF_ENV, "2026-01-01T00:00:00+00:00")
    k = _known(tmp_path)
    k.remember("users/1", known.NAME, "Alice", datetime(2025, 1, 1), "mention")
    k.save()
    assert not (tmp_path / "known.tsv").exists()


def test_bounded_run_ignores_later_spellings(tmp_path, monkeypatch):
    k = _seeded(tmp_path)
    monkeypatch.setenv(config.WORLD_AS_OF_ENV, "2025-09-01T00:00:00+00:00")
    assert k.names_of("users/2") == ["Alice S"]
    assert k.resolve_person("Alice S") == "users/2"
    with pytest.raises(SystemExit, match="no one seen as"):
        k.resolve_person("Alice S DS")


if __name__ == "__main__":
    _shim.run(dict(globals()))
