"""Split a date range into request windows (the API's equivalent of pagination)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Window:
    start: date
    end: date  # inclusive

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"window end {self.end} is before start {self.start}")


def date_windows(start: date, end: date, max_days: int) -> Iterator[Window]:
    """Yield consecutive, non-overlapping windows of at most `max_days` days over [start, end]."""
    if max_days < 1:
        raise ValueError("max_days must be >= 1")
    if end < start:
        return
    current = start
    while current <= end:
        window_end = min(current + timedelta(days=max_days - 1), end)
        yield Window(current, window_end)
        current = window_end + timedelta(days=1)


def ingestion_range(
    watermark: date | None,
    default_start: date,
    lookback_days: int,
    today: date,
) -> tuple[date, date] | None:
    """Decide which dates to request.

    - First run (no watermark): from `default_start`.
    - Later runs: re-read the last `lookback_days` before the watermark, because the source can
      publish late corrections. The load is an upsert, so re-reading is safe.
    Returns None when there is nothing to do.
    """
    start = default_start if watermark is None else watermark - timedelta(days=lookback_days)
    start = max(start, default_start)
    if start > today:
        return None
    return start, today
