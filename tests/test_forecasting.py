"""Tests for the forecasting module (Chunk 6)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics.forecasting import (
    LinearTrendForecaster,
    NotFittedError,
    PriceForecaster,
)


def test_predict_returns_requested_steps(trending_series):
    fc = PriceForecaster().fit(trending_series)
    assert len(fc.predict(steps=8)) == 8
    assert len(fc.predict(steps=4)) == 4


def test_confidence_interval_brackets_point_estimate(trending_series):
    fc = PriceForecaster().fit(trending_series).predict(steps=8)
    assert (fc["lower"] < fc["forecast"]).all()
    assert (fc["forecast"] < fc["upper"]).all()


def test_confidence_interval_widens_with_horizon(trending_series):
    fc = PriceForecaster().fit(trending_series).predict(steps=8)
    width = (fc["upper"] - fc["lower"]).to_numpy()
    # non-decreasing across the horizon (allow tiny float noise)
    assert np.all(np.diff(width) >= -1e-9)
    assert width[-1] > width[0]


def test_short_series_raises_value_error(trending_series):
    with pytest.raises(ValueError):
        PriceForecaster().fit(trending_series.head(5))


def test_predict_before_fit_raises(trending_series):
    with pytest.raises(NotFittedError):
        PriceForecaster().predict()


def test_summary_before_fit_raises():
    with pytest.raises(NotFittedError):
        PriceForecaster().summary()


def test_summary_reports_diagnostics(trending_series):
    summary = PriceForecaster().fit(trending_series).summary()
    assert {"model", "order", "n_obs", "aic", "bic", "params"} <= set(summary)
    assert summary["n_obs"] == len(trending_series)
    assert summary["order"] == (1, 1, 1)


def test_forecast_dates_follow_last_observation(trending_series):
    fc = PriceForecaster().fit(trending_series).predict(steps=8)
    last_obs = trending_series["date"].iloc[-1]
    assert fc["date"].iloc[0] == last_obs + pd.Timedelta(weeks=1)
    assert (fc["date"].diff().dropna() == pd.Timedelta(weeks=1)).all()


def test_linear_forecaster_shares_contract(trending_series):
    fc = LinearTrendForecaster().fit(trending_series).predict(steps=8)
    assert list(fc.columns) == ["date", "forecast", "lower", "upper"]
    assert len(fc) == 8
    assert (fc["lower"] < fc["upper"]).all()
