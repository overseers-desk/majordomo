"""Real-data no-cache e2e: authenticate against Google (reusing a valid token or
logging in) and read one space directly from the API. The mock suite proves
decoder/sieve logic; this proves the OAuth path a user actually hits.

Gated off by default — it needs real Google credentials, which CI and a fresh
checkout do not have. Set ``MAJORDOMO_NOCACHE_E2E=1`` (and have a config with a
token or client secret in place) to run it. With the gate unset it skips
cleanly, no creds required.
"""

import _shim  # noqa: F401

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MAJORDOMO_NOCACHE_E2E") != "1",
    reason="real-data no-cache e2e: set MAJORDOMO_NOCACHE_E2E=1 (needs real Google creds)",
)


def test_nocache_reads_one_space():
    from majordomo import config, nocache, readers

    cfg = config.load_config()
    # Reuse a valid token if present; otherwise mint one via the browser flow.
    token_file = os.path.expanduser(config.nocache_token_file(cfg))
    if not os.path.exists(token_file):
        nocache.login(cfg)

    reader = readers.make_reader(cfg, "nocache")
    rows = reader.spaces()
    assert isinstance(rows, list)
    # A direct read should expose at least one space; if not, that is itself a
    # signal the auth/read path returned nothing.
    assert rows, "no-cache read returned no spaces — check the OAuth client / project"
