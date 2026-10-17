"""What the core raises when it refuses: one line a front door can show as it
stands. ``NotFound`` is a space, person, thread or message the caller named
that matches nothing, a sieve-blocked one included, the two worded alike; a
name that matches several is a plain ``MajordomoError``, so ``--live`` asks
the Chat API only about a miss. ``ScopeError`` is a credential that exists
but lacks the Google scopes it carries in ``.scopes``; ``MajordomoError`` is
every other refusal."""


class MajordomoError(Exception):
    pass


class ScopeError(MajordomoError):
    def __init__(self, message: str, *, scopes: list[str], purpose: str | None = None):
        super().__init__(message)
        self.scopes = list(scopes)
        self.purpose = purpose


class NotFound(MajordomoError):
    pass
