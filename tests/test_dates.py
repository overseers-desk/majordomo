"""Window and explicit-range resolution."""

import _shim  # noqa: F401

from datetime import datetime

from majordomo import dates


def test_all_is_open():
    assert dates.resolve("all") == (None, None)


def test_7d_has_lower_bound_only():
    start, end = dates.resolve("7d")
    assert isinstance(start, datetime) and end is None


def test_month_is_previous_calendar_month():
    start, end = dates.resolve("month")
    assert start.day == 1 and end.day == 1
    assert start < end
    # end is the first of some month; start is the first of the month before it
    assert (end.year, end.month) != (start.year, start.month)


def test_explicit_since_overrides_window():
    start, end = dates.resolve("month", since="2026-01-01")
    assert start == datetime(2026, 1, 1) and end is None


def test_explicit_range():
    start, end = dates.resolve("all", since="2026-01-01", until="2026-02-01")
    assert start == datetime(2026, 1, 1) and end == datetime(2026, 2, 1)


def test_bad_date_exits():
    try:
        dates.resolve("all", since="not-a-date")
    except SystemExit:
        return
    raise AssertionError("bad date should raise SystemExit")


def test_unknown_window_exits():
    try:
        dates.resolve("yesterday")
    except SystemExit:
        return
    raise AssertionError("unknown window should raise SystemExit")


if __name__ == "__main__":
    _shim.run(dict(globals()))
