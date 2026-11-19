from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fx_ingest.client import FrankfurterClient, RetryPolicy
from fx_ingest.sinks import DuckDBSink

BASE_URL = "https://api.test/v1"


def fake_rates(start: date, end: date, symbols: list[str], bump: float = 0.0) -> dict[str, Any]:
    """Imitate the real API: business days only, and a window starting on a weekend also
    returns the previous business day."""
    first = start
    while first.weekday() >= 5:
        first -= timedelta(days=1)
    rates: dict[str, dict[str, float]] = {}
    day = first
    while day <= end:
        if day.weekday() < 5:
            rates[day.isoformat()] = {
                s: round(1 + i * 0.5 + day.toordinal() % 100 / 1000 + bump, 5)
                for i, s in enumerate(symbols)
            }
        day += timedelta(days=1)
    return {
        "amount": 1.0,
        "base": "EUR",
        "start_date": first.isoformat(),
        "end_date": end.isoformat(),
        "rates": rates,
    }


def time_series_handler(bump: float = 0.0) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        span = request.url.path.rsplit("/", 1)[-1]
        start, end = (date.fromisoformat(p) for p in span.split(".."))
        symbols = request.url.params["symbols"].split(",")
        return httpx.Response(200, json=fake_rates(start, end, symbols, bump))

    return handler


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(sleeps: list[float]) -> Iterator[FrankfurterClient]:
    with FrankfurterClient(
        BASE_URL, retry=RetryPolicy(max_attempts=4, base_delay=1, max_delay=8), sleep=sleeps.append
    ) as c:
        yield c


@pytest.fixture
def sink(tmp_path: Path) -> Iterator[DuckDBSink]:
    s = DuckDBSink(tmp_path / "fx.duckdb")
    yield s
    s.close()
