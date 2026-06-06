"""Shared pytest fixtures.

Provides a fresh in-memory repository, a transform pipeline, an EIA-shaped raw
JSON payload, and a small DataFrame with hand-checkable values — so tests run
fast, offline, and deterministically.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from storage.repository import CommodityRepository
from transform.pipeline import CommodityPipeline


@pytest.fixture
def pipeline() -> CommodityPipeline:
    """A stateless transform pipeline."""
    return CommodityPipeline()


@pytest.fixture
def repo():
    """A fresh in-memory SQLite repository, closed after the test."""
    repository = CommodityRepository(":memory:")
    yield repository
    repository.close()


@pytest.fixture
def raw_json() -> dict:
    """An EIA-v2-shaped payload (note: values are strings, like the real API)."""
    return {
        "response": {
            "total": 4,
            "data": [
                {"period": "2024-01-07", "value": "100.0"},
                {"period": "2024-01-14", "value": "110.0"},
                {"period": "2024-01-21", "value": "121.0"},
                {"period": "2024-01-28", "value": "133.1"},
            ],
        }
    }


@pytest.fixture
def known_prices() -> pd.DataFrame:
    """Four weekly prices, each +10% over the previous (easy to verify)."""
    return pd.DataFrame(
        {
            "series_id": "TEST",
            "date": pd.date_range("2024-01-07", periods=4, freq="W"),
            "value": [100.0, 110.0, 121.0, 133.1],
        }
    )


@pytest.fixture
def trending_series() -> pd.DataFrame:
    """A 60-point noisy upward trend for forecasting tests (seeded)."""
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "series_id": "TEST",
            "date": pd.date_range("2024-01-07", periods=60, freq="W"),
            "value": 50 + 0.5 * np.arange(60) + rng.normal(0, 1, 60),
        }
    )


@pytest.fixture
def enriched_with_nans() -> pd.DataFrame:
    """Processed-shaped frame whose warm-up rows carry NaN (rolling stats)."""
    return pd.DataFrame(
        {
            "series_id": "TEST",
            "date": pd.date_range("2024-01-07", periods=5, freq="W"),
            "value": [100.0, 110.0, 121.0, 133.1, 146.4],
            "pct_change": [np.nan, 10.0, 10.0, 10.0, 10.0],
            "rolling_avg_4w": [np.nan, np.nan, np.nan, 116.025, 127.625],
            "rolling_std_4w": [np.nan, np.nan, np.nan, 14.25, 15.6],
            "z_score": [np.nan, np.nan, np.nan, 1.198, 1.2],
            "is_outlier": [False, False, True, False, False],
        }
    )
