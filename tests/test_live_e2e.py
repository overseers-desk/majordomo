"""Real-data live e2e: authenticate against Google (reusing a valid token or
logging in) and read one space live. The mock suite proves decoder/sieve logic;
this proves the OAuth path a user actually hits.

Gated off by default — it needs real Google credentials, which CI and a fresh
checkout do not have. Set ``MAJORDOMO_LIVE_E2E=1`` (and have a config with a
live token or client secret in place) to run it. With the gate unset it skips
cleanly, no creds required.
"""

import _shim  # noqa: F401

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MAJORDOMO_LIVE_E2E") != "1",
    reason="real-data live e2e: set MAJORDOMO_LIVE_E2E=1 (needs real Google creds)",
)


def test_live_reads_one_space():
    from majordomo import config, live, readers

    cfg = config.load_config()
    # Reuse a valid token if present; otherwise mint one via the browser flow.
    token_file = os.path.expanduser(config.live_token_file(cfg))
    if not os.path.exists(token_file):
        live.login(cfg)

    reader = readers.make_reader(cfg, "live")
    rows = reader.spaces()
    assert isinstance(rows, list)
    # A live account should expose at least one space; if not, that is itself a
    # signal the auth/read path returned nothing.
    assert rows, "live read returned no spaces — check the OAuth client / project"
