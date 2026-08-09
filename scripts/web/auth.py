"""Session-based authentication for the MatStat web app.

Authentication is opt-out: it is enabled by default and requires a password
set via ``MATSTAT_PASSWORD``. Setting ``MATSTAT_AUTH=off`` disables it entirely
(for trusted/LAN deployments). When enabled, the app fails fast at startup if
no password is configured — it must never silently serve an unprotected
instance.

Sessions are single: at most one active login is allowed at a time
(``MATSTAT_SESSION_HOURS``, default 12). A second login while one is active is
rejected. Sessions are held in memory and die with the process. The cookie is
a random session id signed with HMAC-SHA256 keyed off the password, so clients
cannot forge or tamper with it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time

from pydantic import BaseModel

COOKIE_NAME: str = "matstat_session"
_DEFAULT_SESSION_HOURS: int = 12

# Config is read once at import from the environment; tests monkeypatch these
# module globals per-test (see tests/conftest.py).
_AUTH_OFF: bool = os.environ.get("MATSTAT_AUTH", "").lower() in ("0", "false", "off", "no")
_PASSWORD: str | None = os.environ.get("MATSTAT_PASSWORD") or None
SESSION_TTL: float = float(os.environ.get("MATSTAT_SESSION_HOURS", _DEFAULT_SESSION_HOURS)) * 3600.0
SECURE_COOKIE: bool = os.environ.get("MATSTAT_SECURE_COOKIE", "").lower() in (
    "1", "true", "yes", "on",
)

# The single active session: (session_id, expires_at). None when nobody is
# logged in. Lives in memory only, so a restart logs everyone out.
_CURRENT_SESSION: tuple[str, float] | None = None


class LoginRequest(BaseModel):
    """Password submitted by the login page."""

    password: str


def _signing_key() -> bytes:
    return hashlib.sha256((_PASSWORD or "").encode("utf-8")).digest()


def _sign(session_id: str) -> str:
    return hmac.new(_signing_key(), session_id.encode("utf-8"), hashlib.sha256).hexdigest()


def enabled() -> bool:
    """True when login is required (authentication not explicitly disabled)."""
    return not _AUTH_OFF


def check_configured() -> None:
    """Fail fast at startup when a password is expected but not configured."""
    if enabled() and not _PASSWORD:
        raise RuntimeError(
            "MatStat authentication is enabled but no password is configured. "
            "Set MATSTAT_PASSWORD to a password, or set MATSTAT_AUTH=off to run "
            "without authentication (trusted networks only)."
        )


def _valid(raw: str) -> str | None:
    """Validate a ``session_id.signature`` cookie value and return the id."""
    if "." not in raw:
        return None
    session_id, signature = raw.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(session_id)):
        return None
    return session_id


def is_authenticated(cookies: dict[str, str]) -> bool:
    """True when the request carries a valid, unexpired session cookie."""
    if not enabled():
        return True
    session_id: str | None = _valid(cookies.get(COOKIE_NAME, ""))
    if session_id is None:
        return False
    if _CURRENT_SESSION is None or _CURRENT_SESSION[0] != session_id:
        return False
    return time.time() < _CURRENT_SESSION[1]


def login(password: str) -> tuple[bool, str, str | None]:
    """Attempt a login, returning ``(ok, detail, cookie_value)``.

    Single-session lock: a second login while one is active is rejected.
    """
    global _CURRENT_SESSION
    if not enabled():
        return True, "ok", None
    if not hmac.compare_digest(password, _PASSWORD or ""):
        return False, "Incorrect password", None
    if _CURRENT_SESSION is not None and time.time() < _CURRENT_SESSION[1]:
        return False, "Another session is already active. Log out first.", None
    session_id: str = secrets.token_urlsafe(32)
    _CURRENT_SESSION = (session_id, time.time() + SESSION_TTL)
    logging.info("MatStat session started.")
    return True, "ok", f"{session_id}.{_sign(session_id)}"


def logout() -> None:
    """Clear the active session."""
    global _CURRENT_SESSION
    _CURRENT_SESSION = None
