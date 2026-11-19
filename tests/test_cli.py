import json
from pathlib import Path

import pytest
import respx

from fx_ingest.cli import main

from .conftest import BASE_URL, time_series_handler


def test_cli_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "fx.duckdb"
    args = [
        "--duckdb-path",
        str(db),
        "--base-url",
        BASE_URL,
        "--symbols",
        "usd, gbp",
        "--start",
        "2024-01-01",
        "--log-level",
        "WARNING",
    ]
    with respx.mock(base_url=BASE_URL) as api:
        api.get(path__regex=r"/.*\.\..*").mock(side_effect=time_series_handler())
        assert main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "ok"
    assert set(summary["per_currency"]) == {"USD", "GBP"}
    assert summary["rows_written"] == summary["rows_fetched"] > 0


def test_cli_returns_1_on_api_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--duckdb-path", str(tmp_path / "fx.duckdb"), "--base-url", BASE_URL]
    with respx.mock(base_url=BASE_URL) as api:
        api.get(path__regex=r"/.*").respond(400, json={"message": "bad request"})
        assert main(args) == 1
    log = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert log["severity"] == "ERROR"
    assert "HTTP 400" in log["error"]


def test_bigquery_sink_requires_project(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="bq-project"):
        main(["--sink", "bigquery"])
