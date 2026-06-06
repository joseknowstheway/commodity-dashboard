"""End-to-end ingestion pipeline orchestrator.

Wires together the ingestion client (Chunk 2), the transform pipeline (Chunk 3),
and the storage repository (Chunk 4) into one runnable command:

    fetch raw  ->  upsert raw  ->  recompute processed from full history  ->  upsert

Usage:
    python run_pipeline.py                          # all commodities, incremental
    python run_pipeline.py --series WTI_CRUDE       # one commodity
    python run_pipeline.py --start 2024-01-01       # explicit history window
    python run_pipeline.py --refresh                # force full re-fetch

Design note — why recompute processed from the *full* raw history:
    Rolling-window analytics (4-week mean/std, z-score) need contiguous history
    to be correct. We ingest raw incrementally (cheap, idempotent), then read the
    entire raw series back from the DB and recompute the processed table over all
    of it. Raw is the source of truth; processed is a derived, rebuildable view.
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta

from config import COMMODITIES, settings
from ingestion.eia_client import EIAClient, EIAAPIError
from storage.repository import CommodityRepository
from transform.pipeline import CommodityPipeline

logger = logging.getLogger("run_pipeline")


@dataclass
class SeriesResult:
    """Per-series outcome, collected for the final summary table."""

    key: str
    fetched: int = 0
    inserted: int = 0
    processed: int = 0
    status: str = "ok"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define and parse the command-line interface."""
    parser = argparse.ArgumentParser(
        description="Fetch, transform, and store EIA commodity prices.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--series",
        nargs="+",
        choices=list(COMMODITIES),
        default=list(COMMODITIES),
        metavar="KEY",
        help="Commodity key(s) to process.",
    )
    parser.add_argument(
        "--start",
        metavar="YYYY-MM-DD",
        help="Inclusive start date. Defaults to LOOKBACK_DAYS before today.",
    )
    parser.add_argument(
        "--end",
        metavar="YYYY-MM-DD",
        help="Inclusive end date. Defaults to today.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force a full re-fetch over the whole window, ignoring stored data.",
    )
    return parser.parse_args(argv)


def _default_start() -> str:
    """The lookback-window start date as an ISO string."""
    return (date.today() - timedelta(days=settings.lookback_days)).isoformat()


def process_series(
    key: str,
    client: EIAClient,
    pipeline: CommodityPipeline,
    repo: CommodityRepository,
    *,
    start: str,
    end: str,
    refresh: bool,
) -> SeriesResult:
    """Run the full fetch->store->recompute flow for a single commodity."""
    commodity = COMMODITIES[key]
    result = SeriesResult(key=key)

    # Decide the fetch window. Incremental by default: start from the latest
    # stored date (re-fetched to catch upstream revisions; dedup is idempotent).
    latest = repo.get_latest_date(commodity.series_id)
    if refresh or latest is None:
        fetch_start = start
    else:
        fetch_start = max(latest, start)
    logger.info("[%s] fetching %s..%s", key, fetch_start, end)

    try:
        raw_json = client.fetch_series(commodity, fetch_start, end)
    except EIAAPIError as exc:
        logger.error("[%s] fetch failed: %s", key, exc)
        result.status = "fetch-failed"
        return result

    # Ingest raw (idempotent).
    cleaned = pipeline.clean(pipeline.parse_raw(raw_json, commodity.series_id))
    result.fetched = len(cleaned)
    result.inserted = repo.upsert_raw(cleaned, commodity.series_id)

    # Recompute processed analytics over the *entire* raw history so rolling
    # windows are correct, then upsert. Skip if nothing changed.
    if result.inserted == 0 and not refresh:
        logger.info("[%s] no new rows; processed table left as-is", key)
        result.status = "up-to-date"
        return result

    full_raw = repo.get_raw(commodity.series_id)
    enriched = pipeline.enrich(pipeline.clean(full_raw))
    result.processed = repo.upsert_processed(enriched, commodity.series_id)
    return result


def print_summary(results: list[SeriesResult], elapsed: float) -> None:
    """Print a tidy summary table of what happened."""
    print("\n" + "=" * 64)
    print(f"{'SERIES':<14}{'FETCHED':>9}{'NEW RAW':>9}{'PROCESSED':>11}  STATUS")
    print("-" * 64)
    for r in results:
        print(f"{r.key:<14}{r.fetched:>9}{r.inserted:>9}{r.processed:>11}  {r.status}")
    print("-" * 64)
    totals = (
        sum(r.fetched for r in results),
        sum(r.inserted for r in results),
        sum(r.processed for r in results),
    )
    print(f"{'TOTAL':<14}{totals[0]:>9}{totals[1]:>9}{totals[2]:>11}")
    print("=" * 64)
    print(f"Elapsed: {elapsed:.2f}s  |  DB: {settings.db_path}\n")


def main(argv: list[str] | None = None) -> int:
    """Entry point: orchestrate all requested series and report results."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args(argv)
    settings.validate()

    start = args.start or _default_start()
    end = args.end or date.today().isoformat()
    logger.info(
        "Pipeline start | series=%s window=%s..%s refresh=%s",
        args.series,
        start,
        end,
        args.refresh,
    )

    began = time.perf_counter()
    client = EIAClient(api_key=settings.eia_api_key)
    pipeline = CommodityPipeline()
    results: list[SeriesResult] = []

    with CommodityRepository(settings.db_path) as repo:
        for key in args.series:
            results.append(
                process_series(
                    key,
                    client,
                    pipeline,
                    repo,
                    start=start,
                    end=end,
                    refresh=args.refresh,
                )
            )

    print_summary(results, time.perf_counter() - began)
    # Non-zero exit if every requested series failed to fetch.
    return 0 if any(r.status != "fetch-failed" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
