import itertools
from datetime import date

import pytest

from fx_ingest.windows import Window, date_windows, ingestion_range


def test_windows_cover_range_without_gaps_or_overlap() -> None:
    windows = list(date_windows(date(2024, 1, 1), date(2024, 12, 31), 90))
    assert windows[0].start == date(2024, 1, 1)
    assert windows[-1].end == date(2024, 12, 31)
    for prev, nxt in itertools.pairwise(windows):
        assert (nxt.start - prev.end).days == 1
    assert all((w.end - w.start).days < 90 for w in windows)
    assert len(windows) == 5  # 366 days / 90


def test_single_day_and_empty_ranges() -> None:
    assert list(date_windows(date(2024, 5, 1), date(2024, 5, 1), 30)) == [
        Window(date(2024, 5, 1), date(2024, 5, 1))
    ]
    assert list(date_windows(date(2024, 5, 2), date(2024, 5, 1), 30)) == []


def test_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        list(date_windows(date(2024, 1, 1), date(2024, 1, 2), 0))
    with pytest.raises(ValueError):
        Window(date(2024, 1, 2), date(2024, 1, 1))


def test_first_run_starts_at_default() -> None:
    assert ingestion_range(None, date(2023, 1, 1), 7, date(2024, 1, 10)) == (
        date(2023, 1, 1),
        date(2024, 1, 10),
    )


def test_later_runs_reread_lookback_days() -> None:
    assert ingestion_range(date(2024, 3, 15), date(2023, 1, 1), 7, date(2024, 3, 20)) == (
        date(2024, 3, 8),
        date(2024, 3, 20),
    )


def test_lookback_never_goes_before_default_start() -> None:
    assert ingestion_range(date(2023, 1, 3), date(2023, 1, 1), 7, date(2023, 1, 5)) == (
        date(2023, 1, 1),
        date(2023, 1, 5),
    )


def test_nothing_to_do_when_start_is_in_the_future() -> None:
    assert ingestion_range(None, date(2030, 1, 1), 7, date(2024, 1, 1)) is None
