"""What the front doors (the CLI and the MCP server) do around the core that
the core must not: Google's consent flow, which needs a browser or a person at
a terminal. ``login`` runs it for every scope majordomo uses; ``up_front``
runs it once at the start of a command whose shape needs a scope the saved
login lacks, when someone can answer. The core only raises ScopeError and
never imports this module.
"""

from __future__ import annotations

import errno
import os
import sys

from . import api, config
from .errors import MajordomoError, ScopeError

# The loopback port the consent flow listens on for Google's redirect.
CONSENT_PORT = 7276
# How long a consent a command opened by itself waits for the person before
# the command carries on without the new scopes.
CONSENT_TIMEOUT = 180


def consent_mode() -> str | None:
    """How a command may ask for a scope it lacks: "browser" when a browser
    can be opened here, "url" when only a person at a terminal can take the
    printed link, None when nobody is there to answer (cron, CI, a headless
    server)."""
    if os.environ.get("CI"):
        return None
    remote = os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY")
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        display = not remote
    else:
        display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if display:
        return "browser"
    try:
        attended = sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        attended = False
    return "url" if attended else None


def _open_quietly(url, new=0, autoraise=True):
    """Open the consent page from a child process whose stdout and stderr are
    discarded, so nothing a browser launcher prints reaches this process's
    stdout: under the MCP server that stream is the protocol."""
    import subprocess

    subprocess.Popen([sys.executable, "-m", "webbrowser", "-t", url],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def grant(cfg: dict, *, open_browser: bool = True, timeout: int | None = None) -> str:
    """Run Google's consent flow for every scope majordomo uses, write the
    token, return its path. Everything the flow prints goes to stderr. With
    ``timeout`` set, an unanswered consent fails after that many seconds
    rather than waiting."""
    import contextlib
    import types
    import webbrowser

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise MajordomoError(
            "majordomo: the Google sign-in library is missing, so this "
            "install is incomplete; reinstall majordomo."
        ) from exc
    client_file = os.path.expanduser(config.api_client_file(cfg))
    token_file = os.path.expanduser(config.api_token_file(cfg))
    if not os.path.exists(client_file):
        raise MajordomoError(
            f"majordomo: no OAuth client at {client_file}. Create a Desktop OAuth "
            "client in Google Cloud (Chat API enabled) and save it there."
        )
    # include_granted_scopes returns what the account already granted this
    # client alongside the new scopes; oauthlib would otherwise reject a token
    # whose scopes differ from the request.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    webbrowser.register("majordomo-quiet", None, types.SimpleNamespace(open=_open_quietly))
    try:
        flow = InstalledAppFlow.from_client_secrets_file(client_file, api.LOGIN_SCOPES)
        with contextlib.redirect_stdout(sys.stderr):
            creds = flow.run_local_server(
                port=CONSENT_PORT, open_browser=open_browser, browser="majordomo-quiet",
                timeout_seconds=timeout,
                authorization_prompt_message="majordomo: grant access in your browser: {url}",
                access_type="offline", prompt="consent", include_granted_scopes="true",
            )
    except Exception as exc:
        # Swallow the raw exception (and its locals, and its message/args):
        # a failed OAuth exchange carries the client secret and authorization
        # code in them.
        raise MajordomoError(
            f"majordomo: login failed or was not completed ({_consent_reason(exc)}). "
            f"Check {client_file} and the Cloud project, then retry `majordomo login`."
        ) from None
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    with open(token_file, "w") as fh:
        fh.write(creds.to_json())
    os.chmod(token_file, 0o600)
    return token_file


def _consent_reason(exc: Exception) -> str:
    """A safe, one-line reason a consent attempt failed: the exception's class
    name, plus a plain sentence for the cases majordomo can tell apart by type
    or errno alone. Never the exception's own message, args, or anything from
    its traceback: a failed OAuth exchange can carry the client secret and
    the authorization code in them, and this must be safe to print (stderr)
    and to carry in the MCP envelope's ``notes`` alike."""
    name = type(exc).__name__
    if name == "WSGITimeoutError":  # google_auth_oauthlib: timeout_seconds elapsed
        return f"{name} (timed out waiting for approval)"
    if isinstance(exc, OSError) and exc.errno == errno.EADDRINUSE:
        return f"{name} (port {CONSENT_PORT} already in use)"
    if name == "AccessDeniedError":  # oauthlib: Google redirected with error=access_denied
        return f"{name} (consent declined / access_denied)"
    return name


def login(cfg: dict) -> str:
    """Mint a token via the browser OAuth flow, write it, return its path."""
    ident = config.identity(cfg)
    if ident:
        raise MajordomoError(
            f"majordomo: {ident['name']} signs in with its service-account key; there is no login."
        )
    return grant(cfg)


def up_front(cfg: dict, **shape) -> None:
    """Before a command reads anything, check that the saved login holds every
    scope its shape needs, and ask Google once for whatever is missing when
    someone can answer. Not asked, declined, failed or unanswered, the command
    carries on: each feature short of its scope falls back or refuses for
    itself, and a failed attempt leaves its safe reason in the notes. A command run as an identity is never asked, the
    app having no consent flow."""
    try:
        api.ensure_command_scopes(cfg, **shape)
        return
    except ScopeError as exc:
        purpose = exc.purpose
    mode = consent_mode()
    if mode is None or config.identity(cfg):
        return
    print(
        "majordomo: this version of majordomo needs more Google permissions than "
        f"your saved login grants ({purpose}); opening Google's consent page to grant them."
        + ("" if mode == "browser" else
           f" Open the link below in a browser on this machine (or forward port {CONSENT_PORT})."),
        file=sys.stderr,
    )
    try:
        grant(cfg, open_browser=(mode == "browser"), timeout=CONSENT_TIMEOUT)
        api.ensure_command_scopes(cfg, **shape)
    except MajordomoError as exc:
        config.note_once(str(exc))
