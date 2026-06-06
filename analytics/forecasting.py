"""Lightweight time-series forecasting for commodity prices.

`PriceForecaster` wraps a statsmodels ARIMA(1,1,1) model — a deliberately simple,
defensible baseline for non-stationary price series. `LinearTrendForecaster` is a
scikit-learn alternative (ordinary least-squares trend line) for comparison.

The goal is to *demonstrate* time-series concepts cleanly, not to build a
production forecasting system.

Example:
    from analytics.forecasting import PriceForecaster
    fc = PriceForecaster().fit(processed_df)
    future = fc.predict(steps=8)          # date, forecast, lower, upper
    diagnostics = fc.summary()            # aic, bic, order, ...
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from statsmodels.tsa.arima.model import ARIMA

logger = logging.getLogger(__name__)

# ARIMA(1,1,1) has 3 parameters; require a small cushion of observations so the
# fit is meaningful rather than degenerate.
MIN_OBSERVATIONS = 10

# Forecast output columns (shared by both forecasters for a consistent contract).
FORECAST_COLUMNS = ["date", "forecast", "lower", "upper"]


class NotFittedError(RuntimeError):
    """Raised when predict()/summary() is called before fit()."""


class PriceForecaster:
    """ARIMA forecaster for a single price series.

    Args:
        order: The (p, d, q) ARIMA order. Defaults to (1, 1, 1): one
            autoregressive term, one order of differencing (prices are
            non-stationary), one moving-average term.
    """

    def __init__(self, order: tuple[int, int, int] = (1, 1, 1)) -> None:
        self.order = order
        self._results = None
        self._last_date: pd.Timestamp | None = None
        self._step: pd.Timedelta = pd.Timedelta(weeks=1)

    def fit(
        self, df: pd.DataFrame, value_col: str = "value", date_col: str = "date"
    ) -> "PriceForecaster":
        """Fit the ARIMA model to a price series.

        Args:
            df: DataFrame containing at least ``date_col`` and ``value_col``.
            value_col: Name of the numeric price column.
            date_col: Name of the date column (used to space the forecast).

        Returns:
            self, so calls can be chained: ``PriceForecaster().fit(df)``.

        Raises:
            ValueError: If there are fewer than ``MIN_OBSERVATIONS`` usable rows.
        """
        clean = df[[date_col, value_col]].dropna()
        if len(clean) < MIN_OBSERVATIONS:
            raise ValueError(
                f"Need >= {MIN_OBSERVATIONS} observations to fit ARIMA"
                f"{self.order}; got {len(clean)}."
            )

        dates = pd.to_datetime(clean[date_col])
        values = clean[value_col].astype(float).to_numpy()
        self._last_date = dates.iloc[-1]
        # Infer the cadence from the data so forecast dates are spaced correctly.
        gaps = dates.diff().dropna()
        if not gaps.empty:
            self._step = gaps.median()

        # Fit on a bare array to avoid statsmodels date-frequency warnings; we
        # rebuild the forecast dates ourselves. Convergence chatter is expected
        # on short/noisy series and is not actionable here.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._results = ARIMA(values, order=self.order).fit()

        logger.info(
            "Fitted ARIMA%s on %d obs (AIC=%.1f)",
            self.order,
            len(values),
            self._results.aic,
        )
        return self

    def predict(self, steps: int = 8, alpha: float = 0.05) -> pd.DataFrame:
        """Forecast future values with confidence-interval bounds.

        Args:
            steps: Number of future periods to forecast.
            alpha: Significance level; ``0.05`` -> a 95% confidence interval.

        Returns:
            DataFrame with columns ``[date, forecast, lower, upper]`` — one row
            per forecast step, dates continuing past the last observed date.
        """
        self._require_fitted()
        forecast = self._results.get_forecast(steps=steps)
        mean = np.asarray(forecast.predicted_mean, dtype=float)
        conf = np.asarray(forecast.conf_int(alpha=alpha), dtype=float)

        future_dates = [self._last_date + self._step * (i + 1) for i in range(steps)]
        return pd.DataFrame(
            {
                "date": future_dates,
                "forecast": mean,
                "lower": conf[:, 0],
                "upper": conf[:, 1],
            }
        )

    def summary(self) -> dict:
        """Return model diagnostics as a plain dict."""
        self._require_fitted()
        params = {
            name: float(value)
            for name, value in zip(self._results.param_names, self._results.params)
        }
        return {
            "model": "ARIMA",
            "order": self.order,
            "n_obs": int(self._results.nobs),
            "aic": float(self._results.aic),
            "bic": float(self._results.bic),
            "params": params,
        }

    def _require_fitted(self) -> None:
        if self._results is None:
            raise NotFittedError("Call fit() before predict()/summary().")


class LinearTrendForecaster:
    """OLS linear-trend forecaster (scikit-learn) — a simple comparison model.

    Fits price against a time index and extrapolates the line. Confidence bounds
    use the normal approximation: forecast +/- z * residual standard deviation.
    """

    def __init__(self) -> None:
        self._model: LinearRegression | None = None
        self._n: int = 0
        self._resid_std: float = 0.0
        self._last_date: pd.Timestamp | None = None
        self._step: pd.Timedelta = pd.Timedelta(weeks=1)

    def fit(
        self, df: pd.DataFrame, value_col: str = "value", date_col: str = "date"
    ) -> "LinearTrendForecaster":
        """Fit an ordinary least-squares trend line to the price series."""
        clean = df[[date_col, value_col]].dropna()
        if len(clean) < 2:
            raise ValueError("Need >= 2 observations to fit a linear trend.")

        dates = pd.to_datetime(clean[date_col])
        y = clean[value_col].astype(float).to_numpy()
        x = np.arange(len(y)).reshape(-1, 1)

        self._model = LinearRegression().fit(x, y)
        residuals = y - self._model.predict(x)
        self._resid_std = float(np.std(residuals, ddof=2)) if len(y) > 2 else 0.0
        self._n = len(y)
        self._last_date = dates.iloc[-1]
        gaps = dates.diff().dropna()
        if not gaps.empty:
            self._step = gaps.median()
        return self

    def predict(self, steps: int = 8, alpha: float = 0.05) -> pd.DataFrame:
        """Extrapolate the trend with a normal-approximation interval."""
        if self._model is None:
            raise NotFittedError("Call fit() before predict().")
        z = 1.959963984540054  # ~95% two-sided normal quantile (alpha=0.05)
        if alpha != 0.05:
            from scipy.stats import norm  # local import; only if customized

            z = float(norm.ppf(1 - alpha / 2))

        future_x = np.arange(self._n, self._n + steps).reshape(-1, 1)
        mean = self._model.predict(future_x)
        margin = z * self._resid_std
        future_dates = [self._last_date + self._step * (i + 1) for i in range(steps)]
        return pd.DataFrame(
            {
                "date": future_dates,
                "forecast": mean,
                "lower": mean - margin,
                "upper": mean + margin,
            }
        )


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import logging as _logging

    from config import COMMODITIES, settings
    from storage.repository import CommodityRepository

    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s: %(message)s")
    commodity = COMMODITIES[settings.default_commodity]
    with CommodityRepository(settings.db_path) as repo:
        df = repo.get_processed(commodity.series_id)

    def _show(frame: pd.DataFrame) -> str:
        out = frame.copy()
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
        num = ["forecast", "lower", "upper"]
        out[num] = out[num].round(3)
        return out.to_string(index=False)

    print(f"\n{commodity.label}: {len(df)} observations")
    fc = PriceForecaster().fit(df)
    print("\nSummary:", fc.summary())
    print("\n8-step forecast:")
    print(_show(fc.predict(steps=8)))

    print("\nLinear trend (comparison):")
    print(_show(LinearTrendForecaster().fit(df).predict(steps=8)))
