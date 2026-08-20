"""The command file lands in the configuration tree the running session reads.

One machine carries several Claude Code configuration trees, and
``CLAUDE_CONFIG_DIR`` names the one in force. A refresh that always wrote to
``~/.claude`` left every other tree without a majordomo command, so a session
started against one of them had no way to reach Google Chat.
"""

import contextlib
import os
import tempfile
from pathlib import Path

import _shim  # noqa: F401

from majordomo import _claude_command


@contextlib.contextmanager
def _config_dir(value):
    """Run the block with CLAUDE_CONFIG_DIR set to ``value``, or unset for None."""
    previous = os.environ.get("CLAUDE_CONFIG_DIR")
    if value is None:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
    else:
        os.environ["CLAUDE_CONFIG_DIR"] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = previous


def test_configured_tree_wins_over_home():
    with tempfile.TemporaryDirectory() as tmp, _config_dir(tmp):
        assert _claude_command.command_file() == Path(tmp) / "commands" / "majordomo.md"


def test_home_is_the_fallback():
    with _config_dir(None):
        expected = Path.home() / ".claude" / "commands" / "majordomo.md"
        assert _claude_command.command_file() == expected


def test_refresh_writes_into_the_configured_tree():
    with tempfile.TemporaryDirectory() as tmp, _config_dir(tmp):
        _claude_command.refresh()
        written = (Path(tmp) / "commands" / "majordomo.md").read_text()
        assert written == _claude_command.COMMAND


def test_a_same_named_skill_supersedes_the_command():
    with tempfile.TemporaryDirectory() as tmp, _config_dir(tmp):
        (Path(tmp) / "skills" / "majordomo").mkdir(parents=True)
        _claude_command.refresh()
        assert not (Path(tmp) / "commands" / "majordomo.md").exists()


if __name__ == "__main__":
    _shim.run(dict(globals()))
