"""Importing majordomo reads and writes nothing under the home directory and
loads none of the front doors (the CLI, the MCP server, their console
output, typer), so a library caller's import depends on nothing in the
host's home. Run in a fresh interpreter whose HOME is an empty directory,
with no XDG or WORLD_AS_OF variable, watched by an audit hook."""

import json
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

PROBE = r"""
import json, os, sys

home = os.environ["HOME"]
touched = []


def under_home(path):
    try:
        path = os.fsdecode(path)
    except TypeError:
        return False
    return path == home or path.startswith(home + os.sep)


def hook(event, args):
    if event in ("open", "os.listdir", "os.scandir", "os.mkdir") and args and under_home(args[0]):
        touched.append([event, os.fsdecode(args[0])])


sys.addaudithook(hook)
import majordomo

print(json.dumps({"touched": touched, "modules": sorted(sys.modules)}))
"""


def test_import_reads_nothing_under_home_and_loads_no_front_door(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": os.defpath, "PYTHONPATH": str(SRC)}
    result = subprocess.run([sys.executable, "-c", PROBE], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    assert seen["touched"] == []
    assert list(home.iterdir()) == []
    loaded = set(seen["modules"])
    assert "majordomo.readers" in loaded
    assert not loaded & {"majordomo.cli", "majordomo.frontdoor", "majordomo.mcp_server", "majordomo.output", "typer"}
