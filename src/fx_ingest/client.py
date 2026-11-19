"""HTTP client for the Frankfurter API with timeouts, retries and exponential backoff."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from fx_ingest.models import RatesResponse
from fx_ingest.windows import Window

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.frankfurter.dev/v1"
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class ApiError(RuntimeError):
    """The API answered with a non-retryable error or retries were exhausted."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 1.0  # seconds; doubles on each attempt
    max_delay: float = 30.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        """Seconds to wait before attempt number `attempt + 1` (attempt starts at 1)."""
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        backoff = min(self.base_delay * 2 ** (attempt - 1), self.max_delay)
        return random.uniform(0, backoff)  # "full jitter": avoids synchronised retries


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse a Retry-After header given in seconds or as an HTTP date."""
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(retry_at.timestamp() - time.time(), 0.0)


class FrankfurterClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 20.0,
        retry: RetryPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "fx-ingest/1.0", "Accept": "application/json"},
        )
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self.requests_made = 0

    def __enter__(self) -> FrankfurterClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        last_error = ""
        for attempt in range(1, self._retry.max_attempts + 1):
            self.requests_made += 1
            retry_after: float | None = None
            try:
                response = self._http.get(path, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 400:
                    return response.json()
                if response.status_code not in RETRYABLE_STATUS:
                    raise ApiError(
                        f"GET {path} -> HTTP {response.status_code}: {response.text[:200]}"
                    )
                last_error = f"HTTP {response.status_code}"
                retry_after = _retry_after_seconds(response)

            if attempt == self._retry.max_attempts:
                break
            wait = self._retry.delay(attempt, retry_after)
            log.warning(
                "retrying request",
                extra={
                    "path": path,
                    "attempt": attempt,
                    "error": last_error,
                    "wait_s": round(wait, 2),
                },
            )
            self._sleep(wait)
        raise ApiError(f"GET {path} failed after {self._retry.max_attempts} attempts: {last_error}")

    def time_series(self, window: Window, base: str, symbols: Sequence[str]) -> RatesResponse:
        params = {"base": base}
        if symbols:
            params["symbols"] = ",".join(symbols)
        payload = self.get_json(f"/{_iso(window.start)}..{_iso(window.end)}", params)
        return RatesResponse.model_validate(payload)


def _iso(day: date) -> str:
    return day.isoformat()
