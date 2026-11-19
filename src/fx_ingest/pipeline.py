"""Orchestration: watermark -> windows -> fetch -> validate -> upsert."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime

from fx_ingest.client import FrankfurterClient
from fx_ingest.models import FxRate
from fx_ingest.sinks import Sink
from fx_ingest.transform import deduplicate, to_rows
from fx_ingest.windows import Window, date_windows, ingestion_range

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunConfig:
    base: str = "EUR"
    symbols: tuple[str, ...] = ("USD", "GBP", "CHF", "JPY", "MXN", "BRL")
    default_start: date = date(2023, 1, 1)
    lookback_days: int = 7
    window_days: int = 90
    dry_run: bool = False


@dataclass
class RunSummary:
    status: str = "ok"
    range_start: str | None = None
    range_end: str | None = None
    windows: int = 0
    requests: int = 0
    rows_fetched: int = 0
    rows_written: int = 0
    watermark_before: str | None = None
    watermark_after: str | None = None
    duration_s: float = 0.0
    dry_run: bool = False
    per_currency: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def run(
    config: RunConfig,
    client: FrankfurterClient,
    sink: Sink,
    *,
    today: date | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunSummary:
    started = time.perf_counter()
    summary = RunSummary(dry_run=config.dry_run)
    today = today or clock().date()

    watermark = sink.watermark(config.base)
    summary.watermark_before = _iso(watermark)
    span = ingestion_range(watermark, config.default_start, config.lookback_days, today)
    if span is None:
        summary.status = "up_to_date"
        summary.watermark_after = summary.watermark_before
        return summary
    summary.range_start, summary.range_end = _iso(span[0]), _iso(span[1])

    ingested_at = clock()
    fetched: list[FxRate] = []
    for window in date_windows(span[0], span[1], config.window_days):
        response = client.time_series(window, config.base, config.symbols)
        rows = list(to_rows(response, window, ingested_at))
        _check_symbols(rows, config.symbols, window)
        log.info(
            "window fetched",
            extra={"start": _iso(window.start), "end": _iso(window.end), "rows": len(rows)},
        )
        fetched.extend(rows)
        summary.windows += 1

    rows = deduplicate(fetched)
    summary.requests = client.requests_made
    summary.rows_fetched = len(rows)
    for row in rows:
        summary.per_currency[row.quote_currency] = (
            summary.per_currency.get(row.quote_currency, 0) + 1
        )

    if not config.dry_run:
        summary.rows_written = sink.upsert(rows)
    summary.watermark_after = _iso(sink.watermark(config.base))
    summary.duration_s = round(time.perf_counter() - started, 3)
    return summary


def _check_symbols(rows: Sequence[FxRate], symbols: Sequence[str], window: Window) -> None:
    """Fail loudly if the API stops returning a requested currency (silent data loss otherwise)."""
    if not rows or not symbols:
        return
    returned = {r.quote_currency for r in rows}
    missing = set(symbols) - returned
    if missing:
        raise ValueError(f"currencies missing from response for {window}: {sorted(missing)}")


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None
