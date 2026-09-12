"""Put ``src/`` on the path so tests run without installing, and give a tiny
standalone runner so a test file works under both ``pytest`` and ``python3``.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
# A test run learns into a throwaway state directory, never the user's own.
os.environ.setdefault("XDG_STATE_HOME", tempfile.mkdtemp(prefix="majordomo-test-"))


def run(namespace: dict) -> None:
    failures = 0
    for name, fn in sorted(namespace.items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    if failures:
        raise SystemExit(f"{failures} test(s) failed")
    print("all passed")
