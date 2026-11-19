"""Targets. Each sink upserts on the primary key, so re-running a window never duplicates rows."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from fx_ingest.models import FxRate

COLUMNS = ("rate_date", "base_currency", "quote_currency", "rate", "source", "ingested_at")


class Sink(Protocol):
    def watermark(self, base: str) -> date | None:
        """Latest rate_date already stored for this base currency."""

    def upsert(self, rows: Sequence[FxRate]) -> int:
        """Insert or update rows; returns the number of rows written."""

    def close(self) -> None: ...


class DuckDBSink:
    """Local target: one DuckDB file, so the project runs end to end without a cloud account."""

    def __init__(self, path: Path, table: str = "fx_rates") -> None:
        import duckdb

        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._table = table
        self._con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                rate_date      DATE        NOT NULL,
                base_currency  VARCHAR(3)  NOT NULL,
                quote_currency VARCHAR(3)  NOT NULL,
                rate           DOUBLE      NOT NULL CHECK (rate > 0),
                source         VARCHAR     NOT NULL,
                ingested_at    TIMESTAMP   NOT NULL,
                PRIMARY KEY (rate_date, base_currency, quote_currency)
            )
            """
        )

    def watermark(self, base: str) -> date | None:
        row = self._con.execute(
            f"SELECT max(rate_date) FROM {self._table} WHERE base_currency = ?", [base]
        ).fetchone()
        return row[0] if row else None

    def upsert(self, rows: Sequence[FxRate]) -> int:
        if not rows:
            return 0
        self._con.executemany(
            f"""
            INSERT INTO {self._table} ({", ".join(COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE SET
                rate = excluded.rate,
                source = excluded.source,
                ingested_at = excluded.ingested_at
            """,
            [_values(r) for r in rows],
        )
        return len(rows)

    def query(self, sql: str) -> list[tuple[Any, ...]]:
        return self._con.execute(sql).fetchall()

    def close(self) -> None:
        self._con.close()


class BigQuerySink:
    """Cloud target: load into a staging table, then MERGE into a date-partitioned table."""

    def __init__(self, project: str, dataset: str, table: str = "fx_rates") -> None:
        from google.cloud import bigquery

        self._bq = bigquery
        self._client = bigquery.Client(project=project)
        self._target = f"{project}.{dataset}.{table}"
        self._staging = f"{project}.{dataset}._{table}_staging"
        self._schema = [
            bigquery.SchemaField("rate_date", "DATE", mode="REQUIRED"),
            bigquery.SchemaField("base_currency", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("quote_currency", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("rate", "NUMERIC", mode="REQUIRED"),
            bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
        ]
        target = bigquery.Table(self._target, schema=self._schema)
        target.time_partitioning = bigquery.TimePartitioning(field="rate_date")
        target.clustering_fields = ["base_currency", "quote_currency"]
        self._client.create_table(target, exists_ok=True)

    def watermark(self, base: str) -> date | None:
        job = self._client.query(
            f"SELECT MAX(rate_date) AS wm FROM `{self._target}` WHERE base_currency = @base",
            job_config=self._bq.QueryJobConfig(
                query_parameters=[self._bq.ScalarQueryParameter("base", "STRING", base)]
            ),
        )
        rows = list(job.result())
        value: date | None = rows[0]["wm"] if rows else None
        return value

    def upsert(self, rows: Sequence[FxRate]) -> int:
        if not rows:
            return 0
        load = self._client.load_table_from_json(
            [r.model_dump(mode="json") for r in rows],
            self._staging,
            job_config=self._bq.LoadJobConfig(
                schema=self._schema, write_disposition="WRITE_TRUNCATE"
            ),
        )
        load.result()
        self._client.query(
            f"""
            MERGE `{self._target}` t
            USING `{self._staging}` s
            ON t.rate_date = s.rate_date
               AND t.base_currency = s.base_currency
               AND t.quote_currency = s.quote_currency
            WHEN MATCHED THEN UPDATE SET
                rate = s.rate, source = s.source, ingested_at = s.ingested_at
            WHEN NOT MATCHED THEN INSERT ROW
            """
        ).result()
        return len(rows)

    def close(self) -> None:
        self._client.delete_table(self._staging, not_found_ok=True)
        self._client.close()


def _values(row: FxRate) -> tuple[Any, ...]:
    return (
        row.rate_date,
        row.base_currency,
        row.quote_currency,
        row.rate,
        row.source,
        row.ingested_at.replace(tzinfo=None),
    )
