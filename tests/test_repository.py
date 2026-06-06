"""Tests for the storage repository (Chunk 4)."""

from __future__ import annotations

import pandas as pd

from storage.repository import CommodityRepository


def test_schema_is_initialized(repo):
    cur = repo._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cur.fetchall()}
    assert {"raw_prices", "processed_prices"} <= tables


def test_upsert_raw_is_idempotent(repo, known_prices):
    first = repo.upsert_raw(known_prices, "T")
    second = repo.upsert_raw(known_prices, "T")
    assert first == len(known_prices)
    assert second == 0  # nothing new the second time
    total = repo._conn.execute(
        "SELECT COUNT(*) FROM raw_prices WHERE series_id='T'"
    ).fetchone()[0]
    assert total == len(known_prices)  # no duplicates


def test_upsert_processed_updates_on_conflict(repo, enriched_with_nans):
    repo.upsert_processed(enriched_with_nans, "T")
    changed = enriched_with_nans.copy()
    changed["value"] = changed["value"] + 1000.0
    repo.upsert_processed(changed, "T")

    got = repo.get_processed("T")
    assert len(got) == len(enriched_with_nans)  # updated, not duplicated
    assert got["value"].iloc[0] == 1100.0  # overwritten


def test_nan_is_stored_as_null(repo, enriched_with_nans):
    repo.upsert_processed(enriched_with_nans, "T")
    row = repo._conn.execute(
        "SELECT pct_change, rolling_avg_4w FROM processed_prices "
        "WHERE date='2024-01-07'"
    ).fetchone()
    assert row[0] is None and row[1] is None


def test_get_processed_respects_date_range(repo, enriched_with_nans):
    repo.upsert_processed(enriched_with_nans, "T")
    got = repo.get_processed("T", start_date="2024-01-21", end_date="2024-02-04")
    assert len(got) == 3
    assert got["date"].min() == pd.Timestamp("2024-01-21")
    assert got["date"].max() == pd.Timestamp("2024-02-04")


def test_get_processed_unknown_series_is_empty(repo):
    got = repo.get_processed("DOES_NOT_EXIST")
    assert got.empty
    assert "value" in got.columns  # correctly-shaped empty frame


def test_is_outlier_round_trips_as_boolean(repo, enriched_with_nans):
    repo.upsert_processed(enriched_with_nans, "T")
    got = repo.get_processed("T")
    assert str(got["is_outlier"].dtype) == "boolean"
    assert bool(got["is_outlier"].iloc[2]) is True


def test_get_latest_date(repo, known_prices):
    assert repo.get_latest_date("T") is None  # empty
    repo.upsert_raw(known_prices, "T")
    assert repo.get_latest_date("T") == "2024-01-28"


def test_get_raw_round_trips(repo, known_prices):
    repo.upsert_raw(known_prices, "T")
    got = repo.get_raw("T")
    assert len(got) == len(known_prices)
    assert list(got.columns) == ["series_id", "date", "value"]
    assert got["value"].tolist() == known_prices["value"].tolist()


def test_empty_dataframe_writes_are_noops(repo):
    empty = pd.DataFrame(columns=["series_id", "date", "value"])
    assert repo.upsert_raw(empty, "T") == 0
    assert repo.upsert_processed(empty, "T") == 0


def test_repository_is_a_context_manager(known_prices):
    with CommodityRepository(":memory:") as r:
        r.upsert_raw(known_prices, "T")
        assert r.get_latest_date("T") == "2024-01-28"
