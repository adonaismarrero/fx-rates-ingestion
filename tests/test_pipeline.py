from datetime import UTC, date, datetime

import pytest
import respx

from fx_ingest.client import FrankfurterClient
from fx_ingest.models import RatesResponse
from fx_ingest.pipeline import RunConfig, run
from fx_ingest.sinks import DuckDBSink
from fx_ingest.transform import deduplicate, to_rows
from fx_ingest.windows import Window

from .conftest import fake_rates, time_series_handler

CONFIG = RunConfig(symbols=("USD", "GBP"), default_start=date(2024, 1, 1), window_days=30)
NOW = datetime(2024, 3, 1, 6, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def test_previous_business_day_is_dropped() -> None:
    window = Window(date(2024, 1, 6), date(2024, 1, 9))  # starts on a Saturday
    response = RatesResponse.model_validate(fake_rates(window.start, window.end, ["USD"]))
    assert date(2024, 1, 5) in response.rates
    days = {r.rate_date for r in to_rows(response, window, NOW)}
    assert days == {date(2024, 1, 8), date(2024, 1, 9)}


def test_deduplicate_keeps_last() -> None:
    window = Window(date(2024, 1, 2), date(2024, 1, 2))
    first = list(
        to_rows(
            RatesResponse.model_validate(fake_rates(window.start, window.end, ["USD"])), window, NOW
        )
    )
    second = list(
        to_rows(
            RatesResponse.model_validate(fake_rates(window.start, window.end, ["USD"], bump=1)),
            window,
            NOW,
        )
    )
    assert deduplicate(first + second) == second


def test_first_run_loads_full_history(
    api: respx.MockRouter, client: FrankfurterClient, sink: DuckDBSink
) -> None:
    api.get(path__regex=r"/\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}").mock(
        side_effect=time_series_handler()
    )
    summary = run(CONFIG, client, sink, today=date(2024, 2, 29), clock=clock)

    business_days = 44  # 2024-01-01 .. 2024-02-29: 23 + 21 weekdays
    assert summary.windows == 2
    assert summary.rows_written == business_days * 2
    assert summary.per_currency == {"GBP": business_days, "USD": business_days}
    assert summary.watermark_before is None
    assert summary.watermark_after == "2024-02-29"
    assert sink.query("SELECT count(*) FROM fx_rates") == [(business_days * 2,)]


def test_rerun_is_idempotent_and_applies_corrections(
    api: respx.MockRouter, client: FrankfurterClient, sink: DuckDBSink
) -> None:
    route = api.get(path__regex=r"/\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}")
    route.mock(side_effect=time_series_handler())
    run(CONFIG, client, sink, today=date(2024, 2, 29), clock=clock)
    before = sink.query("SELECT count(*), sum(rate) FROM fx_rates")

    route.mock(side_effect=time_series_handler(bump=0.01))  # the source "corrects" its rates
    summary = run(CONFIG, client, sink, today=date(2024, 2, 29), clock=clock)

    assert summary.range_start == "2024-02-22"  # 7-day lookback from the watermark
    assert sink.query("SELECT count(*) FROM fx_rates")[0][0] == before[0][0]  # no duplicates
    corrected = sink.query("SELECT count(*) FROM fx_rates WHERE rate_date >= DATE '2024-02-22'")[0][
        0
    ]
    assert sink.query("SELECT sum(rate) FROM fx_rates")[0][0] == pytest.approx(
        before[0][1] + corrected * 0.01
    )


def test_dry_run_writes_nothing(
    api: respx.MockRouter, client: FrankfurterClient, sink: DuckDBSink
) -> None:
    api.get(path__regex=r"/.*\.\..*").mock(side_effect=time_series_handler())
    config = RunConfig(symbols=("USD",), default_start=date(2024, 1, 1), dry_run=True)
    summary = run(config, client, sink, today=date(2024, 1, 31), clock=clock)
    assert summary.rows_fetched > 0
    assert summary.rows_written == 0
    assert sink.query("SELECT count(*) FROM fx_rates") == [(0,)]


def test_missing_currency_fails_loudly(
    api: respx.MockRouter, client: FrankfurterClient, sink: DuckDBSink
) -> None:
    def drops_gbp(request):  # type: ignore[no-untyped-def]
        response = time_series_handler()(request)
        body = response.json()
        for rates in body["rates"].values():
            rates.pop("GBP")
        return type(response)(200, json=body)

    api.get(path__regex=r"/.*\.\..*").mock(side_effect=drops_gbp)
    with pytest.raises(ValueError, match="GBP"):
        run(CONFIG, client, sink, today=date(2024, 1, 31), clock=clock)
    assert sink.query("SELECT count(*) FROM fx_rates") == [(0,)]


def test_up_to_date_when_watermark_is_ahead(client: FrankfurterClient, sink: DuckDBSink) -> None:
    config = RunConfig(default_start=date(2030, 1, 1))
    summary = run(config, client, sink, today=date(2024, 1, 31), clock=clock)
    assert summary.status == "up_to_date"
    assert client.requests_made == 0
