"""Dashboard authentication.

HTTP Basic over a constant-time comparison. Modest by design — this is an
internal admin surface for a small team, and V1 should not ship a half-built
session system. What it must not do is ship *open*: with no password
configured the app refuses to serve rather than falling back to no auth.
"""
from __future__ import annotations

import base64
import hmac
from dataclasses import dataclass

from ..config import DashboardSettings


class AuthNotConfiguredError(RuntimeError):
    """Auth is enabled but no password was set — refuse to start."""


@dataclass
class AuthResult:
    ok: bool
    user: str = ""


class BasicAuth:
    def __init__(self, settings: DashboardSettings):
        self.settings = settings
        if settings.auth_enabled and not settings.password:
            raise AuthNotConfiguredError(
                "DASHBOARD_PASSWORD is not set. Set it, or set "
                "DASHBOARD_AUTH_ENABLED=false to run the dashboard unauthenticated "
                "on a trusted local machine only."
            )

    @property
    def enabled(self) -> bool:
        return self.settings.auth_enabled

    def check(self, header: str | None) -> AuthResult:
        if not self.enabled:
            return AuthResult(ok=True, user="anonymous")
        if not header or not header.lower().startswith("basic "):
            return AuthResult(ok=False)
        try:
            decoded = base64.b64decode(header.split(" ", 1)[1].strip()).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return AuthResult(ok=False)
        user, _, password = decoded.partition(":")
        # Both compared, and always both, so a wrong username and a wrong
        # password take the same time.
        user_ok = hmac.compare_digest(user, self.settings.username)
        password_ok = hmac.compare_digest(password, self.settings.password)
        return AuthResult(ok=user_ok and password_ok, user=user if user_ok else "")
