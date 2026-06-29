"""FreshReader (the `--live` up-to-dateness path): cache base + API top-up, merged.
Stable dimensions (people/spaces) pass straight through to cache; messages/tasks
union the cache rows with API rows newer than the watermark, deduped and sorted.
"""

import _shim  # noqa: F401

from datetime import datetime
from unittest.mock import MagicMock

from majordomo import readers


def _fresh(cache, nc):
    fr = readers.FreshReader(cache, {}, [], [])
    fr._nc = nc  # skip the real NocacheReader build
    return fr


def test_api_start_picks_the_later_bound():
    a, b = datetime(2026, 1, 1), datetime(2026, 2, 1)
    assert readers.FreshReader._api_start(a, b) == b
    assert readers.FreshReader._api_start(None, b) == b
    assert readers.FreshReader._api_start(a, None) == a
    assert readers.FreshReader._api_start(None, None) is None


def test_merge_dedups_by_key_and_sorts():
    base = [{"name": "m1", "create_time": datetime(2026, 1, 1)}]
    fresh = [
        {"name": "m1", "create_time": datetime(2026, 1, 1)},  # already in base -> dropped
        {"name": "m2", "create_time": datetime(2026, 1, 2)},
    ]
    out = readers.FreshReader._merge(base, fresh, key="name", time_key="create_time", reverse=False, limit=10)
    assert [r["name"] for r in out] == ["m1", "m2"]


def test_people_and_spaces_come_straight_from_cache():
    cache = MagicMock()
    cache.people.return_value = ["CACHE_PEOPLE"]
    cache.spaces.return_value = ["CACHE_SPACES"]
    fr = _fresh(cache, MagicMock())
    assert fr.people() == ["CACHE_PEOPLE"]
    assert fr.spaces() == ["CACHE_SPACES"]


def test_tasks_union_cache_and_topup(monkeypatch):
    cache = MagicMock()
    cache.tasks.return_value = [{"source_message_name": "t1", "created_at": datetime(2026, 1, 1)}]
    nc = MagicMock()
    nc.tasks.return_value = [
        {"source_message_name": "t2", "created_at": datetime(2026, 2, 1)},
        {"source_message_name": "t1", "created_at": datetime(2026, 1, 1)},  # dup of cache row
    ]
    monkeypatch.setattr(readers.reports, "space_watermark", lambda conn, sp: None)
    out = _fresh(cache, nc).tasks(space="spaces/X")
    assert [r["source_message_name"] for r in out] == ["t2", "t1"]  # newest first, t1 deduped


def test_unscoped_tasks_polls_only_active_spaces(monkeypatch):
    polled = []
    nc = MagicMock()
    nc.tasks.side_effect = lambda *, space=None, **kw: (polled.append(space), [])[1]
    # Only two spaces are "active"; a dormant one must never be polled.
    monkeypatch.setattr(readers.reports, "active_spaces",
                        lambda conn, blocked, **kw: [("spaces/A", datetime(2026, 6, 1)),
                                                     ("spaces/B", datetime(2026, 5, 1))])
    _fresh(MagicMock(**{"tasks.return_value": []}), nc).tasks()  # unscoped
    assert polled == ["spaces/A", "spaces/B"]


if __name__ == "__main__":
    _shim.run(dict(globals()))
