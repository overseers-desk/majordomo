"""The core refuses by raising MajordomoError, never by exiting: NotFound for a
name that does not resolve, ScopeError for a credential short of a scope. The
CLI turns any of them into one line on stderr and exit status 1, and `--live`
falls back to the Chat API only on NotFound, never on an ambiguous name."""

import _shim  # noqa: F401

import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from majordomo import api, config, readers, roster
from majordomo.cli import app
from majordomo.errors import MajordomoError, NotFound, ScopeError

SRC = Path(__file__).resolve().parent.parent / "src"


def test_every_refusal_is_a_majordomo_error():
    assert issubclass(NotFound, MajordomoError)
    assert issubclass(ScopeError, MajordomoError)
    assert issubclass(MajordomoError, Exception)


def test_a_scope_error_carries_its_scopes():
    exc = ScopeError("majordomo: needs more", scopes=[api.SEND_SCOPE], purpose="sending messages")
    assert str(exc) == "majordomo: needs more"
    assert exc.scopes == [api.SEND_SCOPE] and exc.purpose == "sending messages"
    assert ScopeError("majordomo: needs more", scopes=[]).purpose is None


# --- the CLI's one handler ---------------------------------------------------

@pytest.fixture
def cli(tmp_path, monkeypatch):
    toml = tmp_path / "config.toml"
    toml.write_text("[me]\nuser_id = 'users/1'\n")
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude-here"))
    monkeypatch.delenv("WORLD_AS_OF", raising=False)
    monkeypatch.setattr(api, "ensure_command_scopes", lambda cfg, **flags: None)
    return CliRunner()


@pytest.mark.parametrize("exc", [
    MajordomoError("majordomo: refused."),
    NotFound("majordomo: spaces/X: not found."),
    ScopeError("majordomo: needs more Google permissions.", scopes=[api.SEND_SCOPE]),
])
def test_the_cli_shows_a_refusal_as_one_line_and_exits_1(cli, monkeypatch, exc):
    def refuse(**kw):
        raise exc

    monkeypatch.setattr(readers, "make_reader", _spaces_reader(refuse))
    result = cli.invoke(app, ["spaces"])
    assert result.exit_code == 1
    # CliRunner reports exit 1 for an escaped exception too; SystemExit means the handler ran.
    assert isinstance(result.exception, SystemExit)
    assert result.output == f"{exc}\n"


def _spaces_reader(spaces):
    return lambda cfg, source: types.SimpleNamespace(source="cache", spaces=spaces)


def test_the_notes_a_refused_command_raised_come_before_its_line(cli, monkeypatch):
    def refuse(**kw):
        config.note_once("majordomo: a note.")
        raise NotFound("majordomo: spaces/X: not found.")

    monkeypatch.setattr(readers, "make_reader", _spaces_reader(refuse))
    result = cli.invoke(app, ["spaces"])
    assert result.exit_code == 1
    assert result.output == "majordomo: a note.\nmajordomo: spaces/X: not found.\n"


def test_the_notes_a_command_raised_reach_stderr_by_its_end(cli, monkeypatch):
    def answer(**kw):
        config.note_once("majordomo: a note.")
        return []

    monkeypatch.setattr(readers, "make_reader", _spaces_reader(answer))
    result = cli.invoke(app, ["spaces", "--json"])
    assert result.exit_code == 0
    assert "majordomo: a note.\n" in result.output
    assert config.drain_notes() == []


def test_a_real_run_prints_one_line_on_stderr_and_no_traceback(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "WORLD_AS_OF"}
    env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / "no-claude-here"),
               PYTHONPATH=str(SRC))
    run = subprocess.run([sys.executable, "-m", "majordomo", "spaces"], env=env,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 1
    assert run.stdout == ""
    assert run.stderr.startswith("majordomo: no config at ")
    assert run.stderr.count("\n") == 1


# --- --live falls back on NotFound only ----------------------------------------

def _live(tmp_path):
    ros = roster.Roster(tmp_path)
    ros.learn_space_name("spaces/A", "Ops North")
    ros.learn_space_name("spaces/B", "Ops South")
    ros.learn("users/1", "Al Smith")
    ros.learn("users/2", "Al Jones")
    fresh = readers.FreshReader(readers.CacheReader(None, [], ros=ros), {}, [], [])
    fresh._nc = MagicMock()
    return fresh


def test_live_does_not_ask_the_api_about_an_ambiguous_space(tmp_path):
    fresh = _live(tmp_path)
    with pytest.raises(MajordomoError, match="matches 2 spaces") as ei:
        fresh.resolve_space("ops")
    assert not isinstance(ei.value, NotFound)
    fresh._nc.resolve_space.assert_not_called()


def test_live_does_not_ask_the_api_about_an_ambiguous_person(tmp_path):
    fresh = _live(tmp_path)
    with pytest.raises(MajordomoError, match="matches 2 people") as ei:
        fresh.dm_space_for("al")
    assert not isinstance(ei.value, NotFound)
    fresh._nc.dm_space_for.assert_not_called()


if __name__ == "__main__":
    _shim.run(dict(globals()))
