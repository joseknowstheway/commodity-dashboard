"""Tests for the transformation pipeline (Chunk 3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from transform.pipeline import CommodityPipeline


def test_parse_raw_extracts_records(pipeline, raw_json):
    df = pipeline.parse_raw(raw_json, "RWTC")
    assert list(df.columns) == ["series_id", "date", "value"]
    assert len(df) == 4
    assert (df["series_id"] == "RWTC").all()


def test_parse_raw_empty_returns_typed_frame(pipeline):
    df = pipeline.parse_raw({"response": {"data": []}}, "RWTC")
    assert df.empty
    assert list(df.columns) == ["series_id", "date", "value"]


def test_clean_coerces_types_and_drops_nulls(pipeline):
    raw = {
        "response": {
            "data": [
                {"period": "2024-01-07", "value": "100.0"},
                {"period": "2024-01-14", "value": "bad"},  # unparseable -> dropped
                {"period": "2024-01-21", "value": "120.0"},
            ]
        }
    }
    out = pipeline.clean(pipeline.parse_raw(raw, "T"))
    assert len(out) == 2  # the "bad" row is gone
    assert pd.api.types.is_float_dtype(out["value"])
    assert pd.api.types.is_datetime64_any_dtype(out["date"])


def test_clean_dedupes_keeping_last(pipeline):
    raw = {
        "response": {
            "data": [
                {"period": "2024-01-07", "value": "100.0"},
                {"period": "2024-01-07", "value": "105.0"},  # dup date -> keep last
                {"period": "2024-01-14", "value": "120.0"},
            ]
        }
    }
    out = pipeline.clean(pipeline.parse_raw(raw, "T"))
    assert len(out) == 2
    assert out.loc[out["date"] == "2024-01-07", "value"].iloc[0] == 105.0


def test_pct_change_is_accurate(pipeline, known_prices):
    enriched = pipeline.enrich(pipeline.clean(known_prices))
    expected = pd.Series([np.nan, 10.0, 10.0, 10.0], name="pct_change")
    pd.testing.assert_series_equal(
        enriched["pct_change"].reset_index(drop=True), expected
    )


def test_rolling_avg_correct(pipeline, known_prices):
    enriched = pipeline.enrich(pipeline.clean(known_prices))
    expected_last = (100 + 110 + 121 + 133.1) / 4
    assert enriched["rolling_avg_4w"].iloc[-1] == expected_last


def test_zscore_matches_manual(pipeline, known_prices):
    enriched = pipeline.enrich(pipeline.clean(known_prices))
    vals = [100, 110, 121, 133.1]
    mean = np.mean(vals)
    std = np.std(vals, ddof=1)  # pandas rolling std uses ddof=1
    expected = (133.1 - mean) / std
    assert enriched["z_score"].iloc[-1] == pytest.approx(expected)


def test_rolling_columns_have_warmup_nans(pipeline, known_prices):
    enriched = pipeline.enrich(pipeline.clean(known_prices))
    # window=4: first 3 rows can't have a rolling stat
    assert enriched["rolling_avg_4w"].iloc[:3].isna().all()
    assert enriched["rolling_avg_4w"].iloc[3:].notna().all()


def test_outlier_local_spike_is_flagged(pipeline):
    vals = list(np.linspace(50, 70, 40))
    vals[20] = 200.0  # one obvious spike
    df = pd.DataFrame(
        {
            "series_id": "T",
            "date": pd.date_range("2024-01-07", periods=40, freq="W"),
            "value": vals,
        }
    )
    out = pipeline.clean(df)
    assert list(out.index[out["is_outlier"]]) == [20]


def test_outlier_regime_shift_not_flagged(pipeline):
    # A sustained level change is NOT an outlier (the whole point of Hampel).
    vals = [50, 51, 49, 50, 52, 48, 51] + [100, 101, 99, 100, 102, 98, 101]
    df = pd.DataFrame(
        {
            "series_id": "T",
            "date": pd.date_range("2024-01-07", periods=len(vals), freq="W"),
            "value": vals,
        }
    )
    out = pipeline.clean(df)
    assert int(out["is_outlier"].sum()) == 0


def test_run_empty_input_has_all_columns(pipeline):
    out = pipeline.run({}, "T")
    assert out.empty
    for col in ["pct_change", "rolling_avg_4w", "rolling_std_4w", "z_score"]:
        assert col in out.columns


def test_methods_do_not_mutate_input(pipeline, known_prices):
    before = known_prices.copy()
    pipeline.clean(known_prices)
    pd.testing.assert_frame_equal(known_prices, before)
    # CommodityPipeline import kept for symmetry with other test modules
    assert CommodityPipeline is not None
