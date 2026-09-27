"""Parity: the Python decoder must match the BI project's actual coord
`decode.js` on shared fixtures. Runs the JS via node and compares. The JS is
discovered structurally under ~/code (no hard-coded repo name); the test skips
if node or that file is absent, so it never blocks a machine without them.
"""

import _shim  # noqa: F401

import glob
import json
import os
import shutil
import subprocess

from majordomo import decoder

_matches = glob.glob(os.path.expanduser("~/code/*/spheres/coord/api/decode.js"))
DECODE_JS = _matches[0] if _matches else ""

FIXTURES = [
    {"name": "spaces/AAAA/messages/BBBB.CCCC", "createTime": "2026-06-20T08:15:00.000000Z",
     "text": "Created a task for @John Doe (via Tasks)",
     "annotations": [{"type": "USER_MENTION", "userMention": {"user": {"name": "users/123456789", "type": "HUMAN"}, "type": "MENTION"}}]},
    {"name": "spaces/AAAA/messages/HH.II", "createTime": "2026-06-20T09:00:00.000000Z", "text": "Created a task (via Tasks)"},
    {"name": "spaces/AAAA/messages/DD.EE", "createTime": "2026-06-20T08:20:00.000000Z", "text": "Completed a task (via Tasks)"},
    {"name": "spaces/AAAA/messages/J.K", "createTime": "2026-06-20T08:25:00.000000Z",
     "text": "Created a task for @Jane Roe (P) (via Tasks)",
     "annotations": [{"type": "USER_MENTION", "userMention": {"user": {"name": "users/55"}}}]},
    {"name": "spaces/AAAA/messages/J.L", "createTime": "2026-06-20T08:30:00.000000Z",
     "text": "Changed task assignee from @Jane Roe to @Sam Poe (P) (via Tasks)",
     "annotations": [{"type": "USER_MENTION", "startIndex": 28, "userMention": {"user": {"name": "users/55"}}},
                     {"type": "USER_MENTION", "startIndex": 41, "userMention": {"user": {"name": "users/77"}}}]},
    {"name": "spaces/AAAA/messages/J.M", "createTime": "2026-06-20T08:35:00.000000Z", "text": "Unassigned a task from @Sam Poe (via Tasks)"},
    {"name": "spaces/AAAA/messages/J.N", "createTime": "2026-06-20T08:40:00.000000Z", "text": "Completed a task Created (via Tasks)"},
]

_FIELDS = ("source_message_name", "space_name", "assignee_user_name", "assignee_display", "title", "created_at")


def _node_decode(fixtures: list) -> list:
    """Each fixture through the JS decodeTask, lifecycleOf and assigneeChangeOf."""
    src = (
        "import {decodeTask, lifecycleOf, assigneeChangeOf} from %s;\n"
        "let c='';process.stdin.on('data',d=>c+=d);"
        "process.stdin.on('end',()=>{const fs=JSON.parse(c);process.stdout.write(JSON.stringify("
        "fs.map(f=>({task: decodeTask(f), lifecycle: lifecycleOf(f), assign: assigneeChangeOf(f)}))));});"
    ) % json.dumps(DECODE_JS)
    r = subprocess.run(["node", "--input-type=module", "-e", src],
                       input=json.dumps(fixtures), capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return json.loads(r.stdout)


def test_parity_against_decode_js():
    if not shutil.which("node") or not DECODE_JS:
        print("SKIP: node or coord decode.js not present")
        return
    js = _node_decode(FIXTURES)
    assert len(js) == len(FIXTURES)
    for j, f in zip(js, FIXTURES):
        p = decoder.decode_task(f)
        if p is None:
            assert j["task"] is None, f"python None but js returned {j['task']}"
        else:
            for k in _FIELDS:
                assert (j["task"] or {}).get(k) == p.get(k), f"{k}: js={(j['task'] or {}).get(k)!r} py={p.get(k)!r}"
        assert j["lifecycle"] == decoder.lifecycle_of(f), f"lifecycle: js={j['lifecycle']!r} py={decoder.lifecycle_of(f)!r}"
        assert j["assign"] == decoder.assignee_change_of(f), f"assign: js={j['assign']!r} py={decoder.assignee_change_of(f)!r}"


if __name__ == "__main__":
    _shim.run(dict(globals()))
