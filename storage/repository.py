"""SQLite persistence via the Repository pattern.

`CommodityRepository` is the *only* place in the codebase that knows SQL exists.
Every other layer (pipeline, dashboard, orchestration) talks to this object's
methods and receives plain pandas DataFrames. That decoupling is the whole point
of the pattern: the storage engine could be swapped for Postgres by rewriting
this one file, with no changes anywhere else.

Two tables:
    raw_prices        — immutable ingested observations (date, value).
    processed_prices  — enriched analytics, refreshed when recomputed.

Example:
    from storage.repository import CommodityRepository
    with CommodityRepository("data/commodity.db") as repo:
        repo.upsert_raw(raw_df, "RWTC")
        repo.upsert_processed(enriched_df, "RWTC")
        df = repo.get_processed("RWTC", start_date="2025-01-01")
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Columns persisted to processed_prices (order matters for executemany).
PROCESSED_COLUMNS = [
    "series_id",
    "date",
    "value",
    "pct_change",
    "rolling_avg_4w",
    "rolling_std_4w",
    "z_score",
    "is_outlier",
]

RAW_COLUMNS = ["series_id", "date", "value"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id   TEXT NOT NULL,
    date        TEXT NOT NULL,
    value       REAL,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(series_id, date)
);

CREATE TABLE IF NOT EXISTS processed_prices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id      TEXT NOT NULL,
    date           TEXT NOT NULL,
    value          REAL,
    pct_change     REAL,
    rolling_avg_4w REAL,
    rolling_std_4w REAL,
    z_score        REAL,
    is_outlier     INTEGER,
    processed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(series_id, date)
);

CREATE INDEX IF NOT EXISTS idx_raw_series_date
    ON raw_prices(series_id, date);
CREATE INDEX IF NOT EXISTS idx_processed_series_date
    ON processed_prices(series_id, date);
"""


class CommodityRepository:
    """Repository for commodity price data backed by SQLite.

    Holds a single long-lived connection (opened in ``__init__``) and wraps each
    write in a transaction via the connection's context-manager protocol
    (``with self._conn:`` commits on success, rolls back on exception).

    Args:
        db_path: Filesystem path to the SQLite file, or ``":memory:"`` for an
            ephemeral in-memory database (used by the test suite).
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        # A single connection shared across the app needs check_same_thread off
        # (the Dash dashboard runs callbacks on worker threads). Writes are
        # serialized with a lock to keep that sharing safe.
        self._lock = threading.Lock()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            # WAL improves read/write concurrency for file-backed databases.
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._initialize_schema()

    # ------------------------------------------------------------------ #
    # Schema / lifecycle
    # ------------------------------------------------------------------ #
    def _initialize_schema(self) -> None:
        """Create tables and indexes if they don't already exist."""
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
        logger.info("Schema ready at %s", self.db_path)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "CommodityRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def upsert_raw(self, df: pd.DataFrame, series_id: str) -> int:
        """Insert raw observations idempotently; return the count newly added.

        Raw observations are immutable facts, so this uses ``INSERT OR IGNORE``:
        re-inserting an existing ``(series_id, date)`` is a silent no-op. Running
        the pipeline twice never creates duplicates.
        """
        records = self._to_records(df, RAW_COLUMNS, series_id)
        if not records:
            return 0
        sql = (
            "INSERT OR IGNORE INTO raw_prices (series_id, date, value) VALUES (?, ?, ?)"
        )
        with self._lock, self._conn:
            before = self._conn.total_changes
            self._conn.executemany(sql, records)
            inserted = self._conn.total_changes - before
        logger.info(
            "upsert_raw[%s]: %d new row(s) of %d submitted",
            series_id,
            inserted,
            len(records),
        )
        return inserted

    def upsert_processed(self, df: pd.DataFrame, series_id: str) -> int:
        """Upsert enriched rows; return the number of rows written.

        Unlike raw data, derived analytics *change* when recomputed (e.g. a
        revised price, or more history shifting a rolling window). So this is a
        true upsert — ``ON CONFLICT(series_id, date) DO UPDATE`` — overwriting
        the stored analytics and refreshing ``processed_at``.
        """
        records = self._to_records(df, PROCESSED_COLUMNS, series_id)
        if not records:
            return 0
        cols = ", ".join(PROCESSED_COLUMNS)
        placeholders = ", ".join(["?"] * len(PROCESSED_COLUMNS))
        updates = ", ".join(
            f"{c}=excluded.{c}"
            for c in PROCESSED_COLUMNS
            if c not in ("series_id", "date")
        )
        sql = (
            f"INSERT INTO processed_prices ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(series_id, date) DO UPDATE SET {updates}, "
            f"processed_at=CURRENT_TIMESTAMP"
        )
        with self._lock, self._conn:
            self._conn.executemany(sql, records)
        logger.info("upsert_processed[%s]: %d row(s) written", series_id, len(records))
        return len(records)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def get_processed(
        self,
        series_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Return processed rows for a series as a DataFrame, ordered by date.

        ISO date strings (YYYY-MM-DD) sort lexicographically the same as
        chronologically, so the ``>=``/``<=`` comparisons work directly on the
        TEXT date column.
        """
        query = (
            "SELECT series_id, date, value, pct_change, rolling_avg_4w, "
            "rolling_std_4w, z_score, is_outlier "
            "FROM processed_prices WHERE series_id = ?"
        )
        params: list = [series_id]
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        query += " ORDER BY date ASC"

        with self._lock:
            df = pd.read_sql_query(
                query, self._conn, params=params, parse_dates=["date"]
            )
        if not df.empty:
            # Restore the boolean type SQLite flattened to 0/1 (nullable).
            df["is_outlier"] = df["is_outlier"].astype("boolean")
        return df

    def get_latest_date(self, series_id: str) -> str | None:
        """Return the most recent ingested date for a series, or None if empty.

        The orchestration script uses this to fetch only what's missing instead
        of re-downloading the entire history every run.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT MAX(date) FROM raw_prices WHERE series_id = ?", (series_id,)
            )
            row = cur.fetchone()
        return row[0] if row and row[0] is not None else None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_records(
        df: pd.DataFrame, columns: list[str], series_id: str
    ) -> list[tuple]:
        """Convert a DataFrame into ordered tuples ready for executemany.

        Handles three impedance mismatches between pandas and SQLite:
          * tags every row with ``series_id``;
          * normalizes ``date`` to an ISO string;
          * converts pandas ``NaN``/``NA`` to Python ``None`` so they land as
            SQL ``NULL`` (e.g. the leading rolling-window warm-up rows).
        """
        if df is None or df.empty:
            return []

        out = df.copy()
        out["series_id"] = series_id
        # Add any expected-but-missing columns as nulls (defensive).
        for col in columns:
            if col not in out.columns:
                out[col] = None

        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
        if "is_outlier" in columns:
            # bool -> nullable int (1/0); preserves NA if present.
            out["is_outlier"] = out["is_outlier"].astype("Int64")

        out = out[columns]
        # NaN/NA -> None so SQLite stores NULL rather than the string 'nan'.
        out = out.astype(object).where(pd.notna(out), None)
        return list(out.itertuples(index=False, name=None))


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import logging as _logging

    from config import COMMODITIES, settings
    from ingestion.eia_client import EIAClient
    from transform.pipeline import CommodityPipeline

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings.validate()
    commodity = COMMODITIES[settings.default_commodity]
    raw = EIAClient(api_key=settings.eia_api_key).fetch_series(
        commodity, start_date="2025-01-01"
    )
    pipeline = CommodityPipeline()
    enriched = pipeline.run(raw, commodity.series_id)
    raw_clean = pipeline.clean(pipeline.parse_raw(raw, commodity.series_id))

    with CommodityRepository(":memory:") as repo:
        print("\n-- first write --")
        print("raw inserted     :", repo.upsert_raw(raw_clean, commodity.series_id))
        print(
            "processed written:", repo.upsert_processed(enriched, commodity.series_id)
        )
        print("\n-- second write (idempotency) --")
        print("raw inserted     :", repo.upsert_raw(raw_clean, commodity.series_id))
        print("\nlatest date:", repo.get_latest_date(commodity.series_id))
        got = repo.get_processed(commodity.series_id, start_date="2026-04-01")
        print(f"\nget_processed(>=2026-04-01): {len(got)} rows")
        print(got.tail(4).to_string(index=False))
