"""Apify REST client.

Only Apify's *documented* platform API is used here:

* ``POST /v2/acts/{actor}/runs``      — start a run
* ``GET  /v2/actor-runs/{runId}``     — poll for terminal status
* ``GET  /v2/datasets/{id}/items``    — read the results

No behaviour of any particular actor is assumed. Actor ids and their input
payloads are configuration (see :mod:`social_agent.apify.actors`), so swapping
``apify/facebook-comments-scraper`` for another actor — or replacing Apify with
the Meta Graph API — is a configuration or adapter change, not a rewrite.

This module knows nothing about OpenAI, and the AI layer knows nothing about
this module. They meet only in :mod:`social_agent.agent.pipeline`.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..config import ApifySettings
from ..observability import get_logger

log = get_logger(__name__)

TERMINAL_OK = "SUCCEEDED"
TERMINAL_BAD = {"FAILED", "ABORTED", "TIMED-OUT", "TIMING-OUT"}
RUNNING = {"READY", "RUNNING"}


class ApifyError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class ApifyTimeoutError(ApifyError):
    """An actor run did not reach a terminal state within the budget."""

    def __init__(self, message: str = "Apify actor run timed out"):
        super().__init__(message, retryable=False)


class ApifyActorError(ApifyError):
    """An actor run finished in a non-SUCCEEDED terminal state."""

    def __init__(self, actor_id: str, run_id: str, status: str):
        super().__init__(f"Apify actor {actor_id} run {run_id} ended with status {status}")
        self.actor_id = actor_id
        self.run_id = run_id
        self.status = status


class ApifyNotConfiguredError(ApifyError):
    def __init__(self) -> None:
        super().__init__("APIFY_API_TOKEN is not configured")


class HttpTransport(Protocol):
    """Injectable HTTP seam — tests drive the real client with a fake."""

    def request(self, method: str, url: str, body: dict[str, Any] | None, timeout: int) -> Any:
        ...


class UrllibHttpTransport:
    def request(self, method: str, url: str, body: dict[str, Any] | None, timeout: int) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:600]
            except Exception:  # noqa: BLE001
                pass
            raise ApifyError(
                f"Apify HTTP {exc.code}: {detail or exc.reason}",
                status=exc.code,
                retryable=exc.code == 429 or exc.code >= 500,
            ) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
                raise ApifyTimeoutError(f"Apify request timed out: {url}") from exc
            raise ApifyError(f"Apify network error: {reason}", retryable=True) from exc
        except TimeoutError as exc:
            raise ApifyTimeoutError(f"Apify request timed out: {url}") from exc


@dataclass
class ActorRunResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    status: str = ""
    dataset_id: str = ""


class ApifyClient:
    """Start actor runs and collect their dataset items."""

    def __init__(
        self,
        settings: ApifySettings,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.transport = transport or UrllibHttpTransport()
        self._sleep = sleep
        self._clock = clock

    @property
    def configured(self) -> bool:
        return self.settings.configured

    def _url(self, path: str, params: dict[str, str] | None = None) -> str:
        query = dict(params or {})
        query["token"] = self.settings.api_token
        return f"{self.settings.base_url.rstrip('/')}{path}?{urllib.parse.urlencode(query)}"

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
                 params: dict[str, str] | None = None) -> Any:
        """Issue one API call, retrying only transient transport failures."""
        if not self.configured:
            raise ApifyNotConfiguredError()
        url = self._url(path, params)
        last: ApifyError | None = None
        for attempt in range(1, self.settings.max_retries + 2):
            try:
                return self.transport.request(method, url, body, self.settings.request_timeout_seconds)
            except ApifyError as exc:
                last = exc
                if not exc.retryable or attempt > self.settings.max_retries:
                    raise
                delay = min(2 ** (attempt - 1) + random.uniform(0, 0.4), 15.0)
                log.warning(
                    "apify request failed, retrying",
                    extra={"attempt": attempt, "delay_seconds": round(delay, 2), "error": str(exc)},
                )
                self._sleep(delay)
        raise last or ApifyError("Apify request failed")

    @staticmethod
    def _slug(actor_id: str) -> str:
        # Apify accepts "username~actor-name" in path position.
        return actor_id.replace("/", "~")

    def run_actor(self, actor_id: str, actor_input: dict[str, Any],
                  *, wait_seconds: int | None = None) -> ActorRunResult:
        """Start ``actor_id``, wait for it to finish, return its dataset items.

        Raises :class:`ApifyTimeoutError` if the run has not reached a terminal
        state within the budget, and :class:`ApifyActorError` if it terminated
        unsuccessfully. Neither is retried here: re-running a scraper is a
        decision for the caller, who knows whether it is safe.
        """
        if not actor_id:
            raise ApifyError("No Apify actor configured for this operation")

        budget = wait_seconds or self.settings.timeout_seconds
        started = self._clock()

        run = (self._request("POST", f"/acts/{self._slug(actor_id)}/runs", body=actor_input) or {}).get("data") or {}
        run_id = str(run.get("id", ""))
        dataset_id = str(run.get("defaultDatasetId", ""))
        status = str(run.get("status", ""))
        if not run_id:
            raise ApifyError(f"Apify did not return a run id for actor {actor_id}")

        log.info("apify run started", extra={"actor_id": actor_id, "run_id": run_id})

        while status in RUNNING:
            if self._clock() - started > budget:
                raise ApifyTimeoutError(
                    f"Apify actor {actor_id} run {run_id} exceeded {budget}s"
                )
            self._sleep(self.settings.poll_interval_seconds)
            data = (self._request("GET", f"/actor-runs/{run_id}") or {}).get("data") or {}
            status = str(data.get("status", ""))
            dataset_id = str(data.get("defaultDatasetId", dataset_id))

        if status != TERMINAL_OK:
            raise ApifyActorError(actor_id, run_id, status or "UNKNOWN")

        items: list[dict[str, Any]] = []
        if dataset_id:
            raw = self._request(
                "GET", f"/datasets/{dataset_id}/items",
                params={"clean": "true", "format": "json"},
            )
            if isinstance(raw, list):
                items = [i for i in raw if isinstance(i, dict)]

        log.info(
            "apify run finished",
            extra={"actor_id": actor_id, "run_id": run_id, "status": status, "items": len(items)},
        )
        return ActorRunResult(items=items, run_id=run_id, status=status, dataset_id=dataset_id)
