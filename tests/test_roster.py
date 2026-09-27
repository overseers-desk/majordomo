"""roster.py: the cache files' shape, round trip and atomic write; the People
lookup (batched, the sources asked for, profile name over contact label,
write-back, refetch when stale, soft failure); mention parsing; person and
space resolution (id, email, whole name, substring, ambiguity, none, a backend
seed); the XDG cache path; and WORLD_AS_OF read-only."""

import _shim  # noqa: F401

import json
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from majordomo import config, roster


def _person(pid, *names, email=None):
    """A People API Person: each name is (text, source type)."""
    p = {"resourceName": f"people/{pid}", "names": [
        {"displayName": text, "metadata": {"source": {"type": src, "id": pid}}} for text, src in names]}
    if email:
        p["emailAddresses"] = [{"value": email}]
    return p


def _people_service(directory: dict):
    """A fake People service: getBatchGet answers from ``directory`` and
    records each call's arguments."""
    svc = MagicMock()
    calls = []

    def batch_get(resourceNames=None, personFields=None, sources=None):
        calls.append({"resourceNames": list(resourceNames), "personFields": personFields, "sources": sources})
        req = MagicMock()
        req.execute.return_value = {"responses": [
            {"requestedResourceName": n, "person": directory[n]} if n in directory
            else {"requestedResourceName": n, "httpStatusCode": 404}
            for n in resourceNames]}
        return req

    svc.people().getBatchGet.side_effect = batch_get
    svc.calls = calls
    return svc


def _roster(tmp_path, directory=None):
    svc = _people_service(directory or {})
    return roster.Roster(tmp_path, lookup=lambda: svc), svc


# --- the files ---------------------------------------------------------------

def test_files_are_keyed_by_resource_name_and_keep_the_api_object(tmp_path):
    p = _person("1", ("Ada Quill", "PROFILE"))
    r, _ = _roster(tmp_path, {"people/1": p})
    assert r.names(["users/1"]) == {"users/1": "Ada Quill"}
    r.remember_space({"name": "spaces/A", "displayName": "Kitchen Ops", "spaceType": "SPACE"})
    r.save()
    people = json.loads((tmp_path / "people.json").read_text())
    spaces = json.loads((tmp_path / "spaces.json").read_text())
    assert set(people) == {"people/1"} and people["people/1"]["person"] == p
    assert datetime.strptime(people["people/1"]["fetched_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert spaces["spaces/A"]["space"]["displayName"] == "Kitchen Ops"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["people.json", "spaces.json"]  # no temp left


def test_second_run_reads_the_file_without_a_call(tmp_path):
    r, _ = _roster(tmp_path, {"people/1": _person("1", ("Ada Quill", "PROFILE"))})
    r.names(["users/1"])
    again, svc = _roster(tmp_path)
    assert again.names(["users/1"]) == {"users/1": "Ada Quill"}
    assert svc.calls == []


def test_nothing_learnt_writes_nothing(tmp_path):
    r, _ = _roster(tmp_path)
    r.save()
    assert list(tmp_path.iterdir()) == []


def test_default_path_follows_xdg_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert roster.Roster().dir == tmp_path / "majordomo"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert roster.Roster().dir == Path.home() / ".cache" / "majordomo"


# --- the People lookup -------------------------------------------------------

def test_lookup_batches_by_200_and_asks_every_source(tmp_path):
    r, svc = _roster(tmp_path)
    r.names([f"users/{i}" for i in range(1, 251)])
    assert [len(c["resourceNames"]) for c in svc.calls] == [200, 50]
    assert svc.calls[0]["resourceNames"][0] == "people/1"
    assert svc.calls[0]["personFields"] == "names,emailAddresses,metadata"
    assert set(svc.calls[0]["sources"]) == {
        "READ_SOURCE_TYPE_PROFILE", "READ_SOURCE_TYPE_CONTACT", "READ_SOURCE_TYPE_OTHER_CONTACT"}


def test_only_ids_people_can_hold_are_asked(tmp_path):
    r, svc = _roster(tmp_path)
    assert r.names(["users/app", "users/x@example.com", None]) == {"users/app": None, "users/x@example.com": None}
    assert svc.calls == []


def test_profile_name_beats_a_contact_label(tmp_path):
    p = _person("1", ("Boss", "CONTACT"), ("Ada Quill", "DOMAIN_PROFILE"))
    r, _ = _roster(tmp_path, {"people/1": p, "people/2": _person("2", ("Cy Other", "OTHER_CONTACT"))})
    assert r.names(["users/1", "users/2"]) == {"users/1": "Ada Quill", "users/2": "Cy Other"}
    assert roster.names_in(p) == ["Ada Quill", "Boss"]


def test_a_stale_entry_is_fetched_again(tmp_path):
    r, _ = _roster(tmp_path, {"people/1": _person("1", ("Ada Quill", "PROFILE"))})
    r.names(["users/1"])
    old = (roster._now() - roster.MAX_AGE - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r.people["people/1"]["fetched_at"] = old
    r._dirty.add(roster.PEOPLE_FILE)
    r.save()
    again, svc = _roster(tmp_path, {"people/1": _person("1", ("Ada Quill-Rowe", "PROFILE"))})
    assert again.names(["users/1"]) == {"users/1": "Ada Quill-Rowe"}
    assert len(svc.calls) == 1


def test_no_lookup_leaves_the_id(tmp_path):
    r = roster.Roster(tmp_path, lookup=lambda: None)
    assert r.names(["users/1"]) == {"users/1": None}


def test_insufficient_scope_is_soft_and_says_how_to_grant(tmp_path, capsys):
    svc = MagicMock()
    err = Exception("403")
    err.resp = types.SimpleNamespace(status=403)
    err.content = b'{"error": {"status": "PERMISSION_DENIED", "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}'
    svc.people().getBatchGet.return_value.execute.side_effect = err
    config.drain_notes()
    r = roster.Roster(tmp_path, lookup=lambda: svc)
    assert r.names(["users/1", "users/2"]) == {"users/1": None, "users/2": None}
    assert config.drain_notes() == [roster.LOGIN_NOTE]
    assert "majordomo login" in capsys.readouterr().err


def test_email_resolves_from_the_people_file(tmp_path):
    r, _ = _roster(tmp_path, {"people/3": _person("3", ("Bo Vance", "PROFILE"), email="bo@example.com")})
    r.names(["users/3"])
    assert r.email_of("users/3") == "bo@example.com"
    assert r.resolve_person("BO@example.com") == "users/3"
    with pytest.raises(SystemExit, match="no one known by"):
        r.resolve_person("new@example.com")
    assert r.resolve_person("new@example.com", by_email=lambda e: "users/5") == "users/5"


# --- mentions ------------------------------------------------------------------

ANN = [{"type": "USER_MENTION", "startIndex": 0, "length": 8,
        "userMention": {"user": {"name": "users/1", "type": "HUMAN"}, "type": "MENTION"}},
       {"type": "USER_MENTION", "startIndex": 22, "length": 4,
        "userMention": {"user": {"name": "users/2"}}}]
TEXT = "@Alice B can you ping @Bob"


def test_mentions_from_list_and_json():
    assert roster.mentions_of(TEXT, ANN) == [("users/1", "Alice B"), ("users/2", "Bob")]
    assert roster.mentions_of(TEXT, json.dumps(ANN)) == [("users/1", "Alice B"), ("users/2", "Bob")]


def test_mentions_skip_offsets_without_an_at():
    assert roster.mentions_of("plain text", ANN) == []
    assert roster.mentions_of(TEXT, "not json") == []
    assert roster.mentions_of(None, ANN) == []


# --- resolving people -------------------------------------------------------------

def _seeded(tmp_path):
    r, _ = _roster(tmp_path, {"people/1": _person("1", ("Alice Smith", "PROFILE")),
                              "people/3": _person("3", ("Bob Builder", "PROFILE"))})
    r.names(["users/1", "users/3"])
    # users/2 has no People name: only the run's prose spellings name them.
    r.learn("users/2", "Alice S", datetime(2025, 6, 1))
    r.learn("users/2", "Alice S DS", datetime(2025, 9, 15))
    return r


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
    r = _seeded(tmp_path)
    with pytest.raises(SystemExit, match="no one seen as 'Carol'"):
        r.resolve_person("Carol")
    calls = []

    def seed():
        calls.append(1)
        r.learn("users/4", "Carol Danvers", datetime(2026, 1, 1))

    assert r.resolve_person("Carol", seed=seed) == "users/4"
    assert calls == [1]


def test_spellings_are_never_stored(tmp_path):
    r = _seeded(tmp_path)
    r.save()
    assert "Alice S DS" not in (tmp_path / "people.json").read_text()
    assert roster.Roster(tmp_path).spellings_of("users/2") == []


# --- resolving spaces ---------------------------------------------------------------

def test_space_by_name_and_seed(tmp_path):
    r, _ = _roster(tmp_path)
    assert r.resolve_space("spaces/X") == "spaces/X"
    r.remember_space({"name": "spaces/A", "displayName": "Back Office (Digital)"})
    r.learn_space_name("spaces/B", "Marketing + PR")
    assert r.resolve_space("back office") == "spaces/A"
    with pytest.raises(SystemExit, match="no space named 'Kitchen'"):
        r.resolve_space("Kitchen")
    assert r.resolve_space("Kitchen", seed=lambda: r.learn_space_name("spaces/C", "Kitchen")) == "spaces/C"
    r.learn_space_name("spaces/D", "Marketing")
    assert r.resolve_space("marketing") == "spaces/D"
    with pytest.raises(SystemExit, match="matches 2 spaces"):
        r.resolve_space("market")


def test_a_blocked_space_never_matches_by_name(tmp_path):
    r, _ = _roster(tmp_path)
    r.remember_space({"name": "spaces/P", "displayName": "Private"})
    with pytest.raises(SystemExit, match="no space named"):
        r.resolve_space("Private", blocked=["spaces/P"])


# --- WORLD_AS_OF --------------------------------------------------------------------

def test_bounded_run_serves_names_and_never_writes(tmp_path, monkeypatch):
    monkeypatch.setenv(config.WORLD_AS_OF_ENV, "2026-01-01T00:00:00+00:00")
    r, _ = _roster(tmp_path, {"people/1": _person("1", ("Ada Quill", "PROFILE"))})
    assert r.names(["users/1"]) == {"users/1": "Ada Quill"}
    r.remember_space({"name": "spaces/A", "displayName": "Kitchen Ops"})
    r.save()
    assert list(tmp_path.iterdir()) == []


if __name__ == "__main__":
    _shim.run(dict(globals()))
