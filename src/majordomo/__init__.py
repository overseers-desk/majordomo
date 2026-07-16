"""majordomo — read and report Google Chat task activity from the cache mirror.

The fast path reads an existing server-side MariaDB mirror of Google Chat
(``googlechat_*`` tables) and its already-decoded tasks (``coord_tasks``). This
package is the read-only v1; the direct-API read and own-decoder path is api.py.
"""
