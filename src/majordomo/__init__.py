"""majordomo: read and report Google Chat task activity, and send messages.

The fast path reads an existing server-side MariaDB mirror of Google Chat
(``googlechat_*`` tables) and its already-decoded tasks (``coord_tasks``). The
direct-API read, its task decoder, and send live in api.py.
"""
