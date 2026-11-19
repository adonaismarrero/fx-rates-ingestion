"""Command line entry point. Logs are JSON lines so Cloud Logging can parse them."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from fx_ingest.client import ApiError, FrankfurterClient
from fx_ingest.pipeline import RunConfig, run
from fx_ingest.sinks import BigQuerySink, DuckDBSink, Sink

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"FX_{name}", default)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fx-ingest",
        description="Incrementally load ECB exchange rates into DuckDB or BigQuery. "
        "Every option can also be set with an FX_* environment variable.",
    )
    parser.add_argument("--sink", choices=["duckdb", "bigquery"], default=_env("SINK", "duckdb"))
    parser.add_argument(
        "--duckdb-path", type=Path, default=Path(_env("DUCKDB_PATH", "data/fx.duckdb") or "")
    )
    parser.add_argument("--bq-project", default=_env("BQ_PROJECT"))
    parser.add_argument("--bq-dataset", default=_env("BQ_DATASET", "raw"))
    parser.add_argument("--base", default=_env("BASE", "EUR"))
    parser.add_argument(
        "--symbols",
        default=_env("SYMBOLS", "USD,GBP,CHF,JPY,MXN,BRL"),
        help="comma-separated quote currencies",
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=date.fromisoformat(_env("START", "2023-01-01") or ""),
    )
    parser.add_argument("--lookback-days", type=int, default=int(_env("LOOKBACK_DAYS", "7") or 7))
    parser.add_argument("--window-days", type=int, default=int(_env("WINDOW_DAYS", "90") or 90))
    parser.add_argument("--base-url", default=_env("BASE_URL", "https://api.frankfurter.dev/v1"))
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate, write nothing")
    parser.add_argument("--log-level", default=_env("LOG_LEVEL", "INFO"))
    return parser


def make_sink(args: argparse.Namespace) -> Sink:
    if args.sink == "bigquery":
        if not args.bq_project:
            raise SystemExit("--bq-project (or FX_BQ_PROJECT) is required for the bigquery sink")
        return BigQuerySink(args.bq_project, args.bq_dataset)
    return DuckDBSink(args.duckdb_path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=args.log_level.upper(), handlers=[handler], force=True)
    log = logging.getLogger("fx_ingest")

    config = RunConfig(
        base=args.base.upper(),
        symbols=tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip()),
        default_start=args.start,
        lookback_days=args.lookback_days,
        window_days=args.window_days,
        dry_run=args.dry_run,
    )
    sink = make_sink(args)
    try:
        with FrankfurterClient(args.base_url) as client:
            summary = run(config, client, sink)
    except (ApiError, ValueError) as exc:
        log.error("run failed", extra={"error": str(exc)})
        return 1
    finally:
        sink.close()
    log.info("run finished", extra=summary.as_dict())
    print(json.dumps(summary.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
