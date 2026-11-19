from datetime import date

import httpx
import pytest
import respx

from fx_ingest.client import ApiError, FrankfurterClient, RetryPolicy
from fx_ingest.windows import Window

from .conftest import fake_rates

WINDOW = Window(date(2024, 1, 2), date(2024, 1, 5))
PATH = "/2024-01-02..2024-01-05"


def test_success_is_parsed_and_validated(api: respx.MockRouter, client: FrankfurterClient) -> None:
    route = api.get(PATH).mock(
        return_value=httpx.Response(200, json=fake_rates(WINDOW.start, WINDOW.end, ["USD", "GBP"]))
    )
    response = client.time_series(WINDOW, "EUR", ["USD", "GBP"])
    assert route.calls.last.request.url.params["symbols"] == "USD,GBP"
    assert route.calls.last.request.url.params["base"] == "EUR"
    assert len(response.rates) == 4
    assert client.requests_made == 1


def test_retries_transient_errors_then_succeeds(
    api: respx.MockRouter, client: FrankfurterClient, sleeps: list[float]
) -> None:
    api.get(PATH).mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectTimeout("slow"),
            httpx.Response(200, json=fake_rates(WINDOW.start, WINDOW.end, ["USD"])),
        ]
    )
    client.time_series(WINDOW, "EUR", ["USD"])
    assert client.requests_made == 3
    assert len(sleeps) == 2
    assert 0 <= sleeps[0] <= 1 and 0 <= sleeps[1] <= 2  # full jitter, exponential cap


def test_honours_retry_after_header(
    api: respx.MockRouter, client: FrankfurterClient, sleeps: list[float]
) -> None:
    api.get(PATH).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json=fake_rates(WINDOW.start, WINDOW.end, ["USD"])),
        ]
    )
    client.time_series(WINDOW, "EUR", ["USD"])
    assert sleeps == [3.0]


def test_retry_after_is_capped(
    api: respx.MockRouter, client: FrankfurterClient, sleeps: list[float]
) -> None:
    api.get(PATH).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "600"}),
            httpx.Response(200, json=fake_rates(WINDOW.start, WINDOW.end, ["USD"])),
        ]
    )
    client.time_series(WINDOW, "EUR", ["USD"])
    assert sleeps == [8]


def test_gives_up_after_max_attempts(
    api: respx.MockRouter, client: FrankfurterClient, sleeps: list[float]
) -> None:
    api.get(PATH).mock(return_value=httpx.Response(500))
    with pytest.raises(ApiError, match="after 4 attempts: HTTP 500"):
        client.time_series(WINDOW, "EUR", ["USD"])
    assert client.requests_made == 4
    assert len(sleeps) == 3  # no sleep after the last attempt


def test_client_errors_are_not_retried(api: respx.MockRouter, client: FrankfurterClient) -> None:
    api.get(PATH).mock(return_value=httpx.Response(404, json={"message": "not found"}))
    with pytest.raises(ApiError, match="HTTP 404"):
        client.time_series(WINDOW, "EUR", ["USD"])
    assert client.requests_made == 1


def test_invalid_payload_is_rejected(api: respx.MockRouter, client: FrankfurterClient) -> None:
    payload = fake_rates(WINDOW.start, WINDOW.end, ["USD"])
    payload["rates"]["2024-01-03"]["USD"] = -1
    api.get(PATH).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(ValueError, match="non-positive rate"):
        client.time_series(WINDOW, "EUR", ["USD"])


def test_backoff_without_jitter_bounds() -> None:
    policy = RetryPolicy(base_delay=1, max_delay=10)
    for attempt, cap in [(1, 1), (2, 2), (3, 4), (4, 8), (5, 10), (9, 10)]:
        assert 0 <= policy.delay(attempt) <= cap
    assert policy.delay(1, retry_after=2.5) == 2.5
