"""WSGI application serving the admin dashboard and its JSON API.

WSGI rather than a framework: it runs under ``wsgiref`` for local development
with zero dependencies, and under gunicorn/uWSGI in production without a code
change. The routing table is small enough that a framework would add
dependencies without removing work.
"""
from __future__ import annotations

import json
import traceback
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable

from ..observability import get_logger, request_context
from .api import ROUTES
from .auth import BasicAuth

log = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 256 * 1024

SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    # The UI is a single self-contained page: no external origins at all.
    ("Content-Security-Policy",
     "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
     "img-src data:; connect-src 'self'; form-action 'none'; base-uri 'none'"),
]


class DashboardApp:
    """The WSGI callable."""

    def __init__(self, application):
        self.app = application
        self.auth = BasicAuth(application.settings.dashboard)

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _json(start_response, payload: Any, status: str = "200 OK") -> Iterable[bytes]:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            *SECURITY_HEADERS,
        ]
        start_response(status, headers)
        return [body]

    @staticmethod
    def _html(start_response, html: str, status: str = "200 OK") -> Iterable[bytes]:
        body = html.encode("utf-8")
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            *SECURITY_HEADERS,
        ]
        start_response(status, headers)
        return [body]

    def _unauthorized(self, start_response) -> Iterable[bytes]:
        body = b'{"error":"authentication required"}'
        start_response(
            "401 Unauthorized",
            [
                ("Content-Type", "application/json"),
                ("WWW-Authenticate", 'Basic realm="North Nepal Social Agent"'),
                ("Content-Length", str(len(body))),
                *SECURITY_HEADERS,
            ],
        )
        return [body]

    @staticmethod
    def _read_body(environ) -> dict[str, Any]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            return {}
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        raw = environ["wsgi.input"].read(length)
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    # -- WSGI ------------------------------------------------------------

    def __call__(self, environ, start_response) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/") or "/"

        auth_result = self.auth.check(environ.get("HTTP_AUTHORIZATION"))
        if not auth_result.ok:
            return self._unauthorized(start_response)

        if method == "GET" and path in ("/", "/index.html"):
            index = STATIC_DIR / "index.html"
            if not index.is_file():
                return self._json(start_response, {"error": "dashboard UI missing"}, "500 Internal Server Error")
            return self._html(start_response, index.read_text(encoding="utf-8"))

        if method == "GET" and path == "/healthz":
            return self._json(start_response, {"ok": True})

        handler: Callable | None = ROUTES.get((method, path))
        if handler is None:
            return self._json(start_response, {"error": "not found", "path": path}, "404 Not Found")

        params = {
            k: v[0] for k, v in
            urllib.parse.parse_qs(environ.get("QUERY_STRING", "")).items()
        }

        with request_context() as rid:
            try:
                body = self._read_body(environ) if method == "POST" else {}
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json(
                    start_response, {"error": f"invalid request body: {exc}"}, "400 Bad Request"
                )

            try:
                result = handler(self.app, params, body)
            except Exception as exc:  # noqa: BLE001 - the API must not leak a traceback
                log.exception("dashboard handler failed", extra={"path": path})
                self.app.repos.audit.log(
                    "dashboard.error", level="error",
                    details={"path": path, "error": str(exc),
                             "traceback": traceback.format_exc(limit=5)},
                )
                return self._json(
                    start_response,
                    {"error": "internal error", "request_id": rid},
                    "500 Internal Server Error",
                )

            if isinstance(result, dict):
                result.setdefault("request_id", rid)
            return self._json(start_response, result)


def create_wsgi_app(application=None):
    """Build the WSGI callable, constructing the application if not supplied."""
    if application is None:
        from ..app import build_application

        application = build_application()
    return DashboardApp(application)


def serve(application=None, host: str | None = None, port: int | None = None) -> None:
    """Run the development server (``wsgiref``).

    Fine for a small internal team on a trusted network. For production put it
    behind gunicorn and a TLS-terminating reverse proxy — see the README.
    """
    from wsgiref.simple_server import make_server

    from ..app import build_application

    application = application or build_application()
    dashboard = application.settings.dashboard
    bind_host = host or dashboard.host
    bind_port = port or dashboard.port

    wsgi_app = DashboardApp(application)
    log.info(
        "dashboard listening",
        extra={"host": bind_host, "port": bind_port, "auth": wsgi_app.auth.enabled},
    )
    print(f"North Nepal Social Agent dashboard → http://{bind_host}:{bind_port}")
    if not wsgi_app.auth.enabled:
        print("WARNING: dashboard authentication is DISABLED. Do not expose this port.")
    with make_server(bind_host, bind_port, wsgi_app) as server:
        server.serve_forever()
