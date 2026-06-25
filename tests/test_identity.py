"""Identity and sieve config accessors. Email cannot drive filters in v1, so the
CLI keys off ``[me].user_id``; these check the extraction the CLI relies on.
"""

import _shim  # noqa: F401

from majordomo import config


def test_user_id_present():
    assert config.me_user_id({"me": {"user_id": "users/123"}}) == "users/123"


def test_user_id_absent_even_with_email():
    assert config.me_user_id({"me": {"google_id": "you@example.com"}}) is None
    assert config.me_user_id({}) is None


def test_google_id_is_kept_for_later():
    assert config.me_google_id({"me": {"google_id": "you@example.com"}}) == "you@example.com"


def test_block_spaces():
    assert config.block_spaces({"sieve": {"block_spaces": ["spaces/A", "spaces/B"]}}) == ["spaces/A", "spaces/B"]
    assert config.block_spaces({}) == []


if __name__ == "__main__":
    _shim.run(dict(globals()))
