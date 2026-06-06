"""Transformation pipeline: raw EIA JSON -> clean, analytics-ready DataFrame.

`CommodityPipeline` turns the raw JSON the ingestion client returns into a tidy
pandas DataFrame with derived analytics columns. Every method is **pure** —
it takes input and returns a new DataFrame without mutating its argument or any
shared state — which makes each step trivial to unit-test in isolation.

Pipeline flow:
    parse_raw(raw_json, series_id)   # dict        -> DataFrame[series_id,date,value]
        -> clean(df)                 # cast/sort/dedupe/flag outliers
        -> enrich(df)                # add pct_change, rolling stats, z-score
    run(raw_json, series_id)         # the three above, composed

Example:
    from transform.pipeline import CommodityPipeline
    df = CommodityPipeline().run(raw_json, series_id="RWTC")
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Columns produced by parse_raw (the pipeline's input schema).
BASE_COLUMNS = ["series_id", "date", "value"]

# Rolling-window size, in periods. Data is weekly, so 4 ≈ one month.
ROLLING_WINDOW = 4

# Outlier detection (Hampel filter): a centered rolling window of local
# context, a 3.5-sigma threshold (Iglewicz & Hoaglin 1993), and the 1.4826
# scale factor that makes MAD a consistent estimator of std for normal data.
OUTLIER_WINDOW = 7
OUTLIER_THRESHOLD = 3.5
MAD_SCALE = 1.4826


class CommodityPipeline:
    """Transforms raw EIA price JSON into an enriched DataFrame.

    Stateless: the same instance can process any number of series. Methods do
    not mutate their inputs; each returns a fresh DataFrame.
    """

    def parse_raw(self, raw_json: dict, series_id: str) -> pd.DataFrame:
        """Extract (date, value) records for one series into a DataFrame.

        Args:
            raw_json: The full JSON dict from ``EIAClient.fetch_series`` (with
                the top-level ``response`` envelope).
            series_id: Stable series identifier to tag every row with.

        Returns:
            DataFrame with columns ``[series_id, date, value]``. ``date`` and
            ``value`` are left as raw strings here; type-casting is ``clean``'s
            job. Returns an empty (correctly-typed) frame if the payload has no
            data, so downstream code never has to special-case ``None``.
        """
        records = (raw_json or {}).get("response", {}).get("data", [])
        if not records:
            logger.warning("parse_raw: no data rows for series_id=%s", series_id)
            return pd.DataFrame(columns=BASE_COLUMNS)

        df = pd.DataFrame(records)
        # The EIA payload carries many descriptive columns; keep only what we
        # need and rename to our schema.
        df = df[["period", "value"]].rename(columns={"period": "date"})
        df.insert(0, "series_id", series_id)
        logger.info("parse_raw: %d rows for series_id=%s", len(df), series_id)
        return df[BASE_COLUMNS]

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """Type-cast, sort, de-duplicate, drop nulls, and flag outliers.

        Steps:
            1. Cast ``date`` to datetime and ``value`` to float (bad values ->
               NaN via ``errors="coerce"``).
            2. Drop rows whose ``value`` could not be parsed (unusable).
            3. Sort by date ascending and drop duplicate dates (keep the last).
            4. Add a boolean ``is_outlier`` column using a robust MAD-based
               modified z-score. Outliers are *flagged, not removed*, so the
               dashboard can still surface them.

        Returns a new, index-reset DataFrame; the input is not modified.
        """
        if df.empty:
            return df.assign(is_outlier=pd.Series(dtype=bool))

        out = df.copy()
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out["value"] = pd.to_numeric(out["value"], errors="coerce")

        before = len(out)
        out = out.dropna(subset=["date", "value"])
        dropped = before - len(out)
        if dropped:
            logger.warning("clean: dropped %d row(s) with null date/value", dropped)

        out = out.sort_values("date").drop_duplicates(
            subset=["series_id", "date"], keep="last"
        )
        out = out.reset_index(drop=True)

        out["is_outlier"] = self._flag_outliers(out["value"])
        n_out = int(out["is_outlier"].sum())
        if n_out:
            logger.info("clean: flagged %d outlier(s)", n_out)
        return out

    def enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add derived analytics columns to a cleaned DataFrame.

        Adds:
            * ``pct_change``     — period-over-period % change in ``value``.
            * ``rolling_avg_4w`` — trailing 4-period mean.
            * ``rolling_std_4w`` — trailing 4-period standard deviation.
            * ``z_score``        — (value - rolling mean) / rolling std, i.e.
              how many std devs the current price sits from its recent trend.

        Assumes ``df`` is already sorted ascending by date (``clean`` does
        this). The first ``ROLLING_WINDOW - 1`` rows will have NaN rolling
        stats by definition — not enough history yet.
        """
        if df.empty:
            extra = ["pct_change", "rolling_avg_4w", "rolling_std_4w", "z_score"]
            return df.assign(**{c: pd.Series(dtype="float64") for c in extra})

        out = df.copy()
        value = out["value"]

        out["pct_change"] = value.pct_change() * 100.0

        rolling = value.rolling(window=ROLLING_WINDOW)
        out["rolling_avg_4w"] = rolling.mean()
        out["rolling_std_4w"] = rolling.std()

        # Standardize against the trailing window. Guard against /0 (a flat
        # window has std 0 -> inf); represent "undefined" as NaN.
        with np.errstate(divide="ignore", invalid="ignore"):
            z = (value - out["rolling_avg_4w"]) / out["rolling_std_4w"]
        out["z_score"] = z.replace([np.inf, -np.inf], np.nan)

        logger.info("enrich: added analytics columns for %d rows", len(out))
        return out

    def run(self, raw_json: dict, series_id: str) -> pd.DataFrame:
        """Compose parse_raw -> clean -> enrich for one series."""
        return self.enrich(self.clean(self.parse_raw(raw_json, series_id)))

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _flag_outliers(values: pd.Series) -> pd.Series:
        """Robust *local* outlier flag via a Hampel filter.

        For each point, compare it to the median of a centered rolling window
        and scale by that window's median absolute deviation (MAD). A point is
        an outlier if it sits more than ``OUTLIER_THRESHOLD`` robust sigmas from
        its local neighbors.

        Why local, not global? Commodity prices trend and shift regimes. A
        *global* median/MAD flags an entire sustained price move as outliers
        (it mistakes a new level for anomalies). A rolling window detects only
        points that deviate from their immediate neighbors — genuine spikes or
        bad data — which is what "outlier" should mean for a time series.

        The window is centered, so this uses look-ahead and is appropriate for
        offline cleaning, not real-time/forecasting decisions.
        """
        if len(values) < 3:
            return pd.Series(False, index=values.index)

        roll = values.rolling(OUTLIER_WINDOW, center=True, min_periods=3)
        local_median = roll.median()
        abs_dev = (values - local_median).abs()
        local_mad = abs_dev.rolling(OUTLIER_WINDOW, center=True, min_periods=3).median()

        threshold = OUTLIER_THRESHOLD * MAD_SCALE * local_mad
        # Where MAD is 0/NaN (flat or too-short window), don't flag.
        flags = abs_dev > threshold
        return flags.fillna(False).astype(bool)


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import logging as _logging

    from config import COMMODITIES, settings
    from ingestion.eia_client import EIAClient

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings.validate()
    commodity = COMMODITIES[settings.default_commodity]
    raw = EIAClient(api_key=settings.eia_api_key).fetch_series(
        commodity, start_date="2025-01-01"
    )
    result = CommodityPipeline().run(raw, series_id=commodity.series_id)
    pd.set_option("display.width", 120)
    pd.set_option("display.max_columns", None)
    print(f"\n{commodity.label}: {len(result)} rows. Tail:")
    print(result.tail(6).to_string(index=False))
