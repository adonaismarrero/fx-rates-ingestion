"""Turn API responses into target rows."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime

from fx_ingest.models import FxRate, RatesResponse
from fx_ingest.windows import Window


def to_rows(response: RatesResponse, window: Window, ingested_at: datetime) -> Iterator[FxRate]:
    """Flatten {date: {currency: rate}} into rows.

    The API answers a window that starts on a weekend or holiday with the previous business day
    (e.g. 2024-01-01..05 also returns 2023-12-29). Those dates belong to another window, so they
    are dropped here to keep every window's output independent and deterministic.
    """
    if response.amount != 1:
        raise ValueError(f"expected rates for amount=1, got amount={response.amount}")
    for day in sorted(response.rates):
        if not window.start <= day <= window.end:
            continue
        for currency in sorted(response.rates[day]):
            yield FxRate(
                rate_date=day,
                base_currency=response.base,
                quote_currency=currency,
                rate=response.rates[day][currency],
                ingested_at=ingested_at,
            )


def deduplicate(rows: Iterable[FxRate]) -> list[FxRate]:
    """Keep the last row per primary key (a later window wins)."""
    by_key: dict[tuple[date, str, str], FxRate] = {}
    for row in rows:
        by_key[row.key] = row
    return [by_key[k] for k in sorted(by_key)]
