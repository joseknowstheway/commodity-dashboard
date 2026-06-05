"""EIA v2 API client.

A small, reusable, object-oriented client for fetching commodity price series
from the U.S. Energy Information Administration (EIA) v2 REST API.

Responsibilities (and *only* these — parsing/storage live in other layers):
    * Build correctly-shaped v2 requests (route + series facet + date window).
    * Send them with a timeout.
    * Retry transient failures (timeouts, connection errors, 429, 5xx) with
      exponential backoff + jitter.
    * Fail fast and clearly on non-retryable errors (4xx, bad JSON).
    * Log what it does via the `logging` module (never `print`).

Example:
    from config import settings, COMMODITIES
    from ingestion.eia_client import EIAClient

    client = EIAClient(api_key=settings.eia_api_key)
    raw = client.fetch_series(COMMODITIES["WTI_CRUDE"], start_date="2024-01-01")
"""

from __future__ import annotations

import logging
import random
import time
from typing import Iterable

import requests

from config import Commodity, settings

logger = logging.getLogger(__name__)

# HTTP status codes that are worth retrying. 429 = rate limited; 5xx = server
# side. Everything else in the 4xx range is a client error that won't fix
# itself, so we don't retry those.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class EIAAPIError(RuntimeError):
    """Raised when the EIA API cannot be queried successfully.

    Wrapping failures in one project-specific exception lets callers (the
    pipeline, the orchestration script) catch a single type instead of a
    grab-bag of requests/JSON exceptions.
    """


class EIAClient:
    """Object-oriented client for the EIA v2 data API.

    Args:
        api_key: EIA API key (free from https://www.eia.gov/opendata/).
        base_url: API root, e.g. "https://api.eia.gov/v2".
        timeout: Per-request timeout in seconds.
        max_retries: Number of retry attempts after the first try for
            transient failures.
        backoff_factor: Base for exponential backoff; the sleep before retry
            *n* (0-indexed) is ``backoff_factor * 2**n`` seconds plus jitter.
        session: Optional pre-built ``requests.Session`` (handy for tests).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = settings.eia_base_url,
        timeout: float = 20.0,
        max_retries: int = 4,
        backoff_factor: float = 0.5,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("EIAClient requires a non-empty api_key.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        # Reusing one Session enables HTTP connection pooling (keep-alive),
        # which is faster than a fresh connection per request.
        self.session = session or requests.Session()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def fetch_series(
        self,
        commodity: Commodity,
        start_date: str | None = None,
        end_date: str | None = None,
        length: int = 5000,
    ) -> dict:
        """Fetch one commodity's price series and return the raw JSON dict.

        Args:
            commodity: Catalog entry describing the route + series facet.
            start_date: Inclusive ISO date (YYYY-MM-DD) lower bound, or None.
            end_date: Inclusive ISO date (YYYY-MM-DD) upper bound, or None.
            length: Max rows to request (EIA caps a single page at 5000).

        Returns:
            The parsed JSON response as a dict (the full payload, including
            the top-level ``response`` envelope). Parsing into a DataFrame is
            the transform layer's job, not the client's.

        Raises:
            EIAAPIError: On any non-recoverable HTTP, network, or JSON error.
        """
        url = f"{self.base_url}/{commodity.route}/data/"
        params = self._build_params(commodity, start_date, end_date, length)
        logger.info(
            "Fetching %s (series=%s, route=%s, %s..%s)",
            commodity.key,
            commodity.series_id,
            commodity.route,
            start_date or "earliest",
            end_date or "latest",
        )
        payload = self._get(url, params)

        total = payload.get("response", {}).get("total")
        n = len(payload.get("response", {}).get("data", []))
        logger.info(
            "Fetched %s rows for %s (API reports total=%s)", n, commodity.key, total
        )
        if total is not None and str(total).isdigit() and int(total) > length:
            # We don't paginate here: our weekly windows are well under 5000
            # rows. Flag it loudly so a future maintainer knows the limit.
            logger.warning(
                "%s has %s rows but we requested length=%s; results truncated.",
                commodity.key,
                total,
                length,
            )
        return payload

    def fetch_multiple(
        self,
        commodities: Iterable[Commodity],
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, dict]:
        """Fetch several series, returning a dict keyed by series_id.

        One series failing does not abort the rest: failures are logged and
        skipped so a single bad request can't sink the whole pipeline run.
        """
        results: dict[str, dict] = {}
        for commodity in commodities:
            try:
                results[commodity.series_id] = self.fetch_series(
                    commodity, start_date, end_date
                )
            except EIAAPIError:
                logger.exception("Skipping %s after fetch failure", commodity.key)
        return results

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _build_params(
        self,
        commodity: Commodity,
        start_date: str | None,
        end_date: str | None,
        length: int,
    ) -> dict:
        """Assemble EIA v2 query parameters (sorted oldest-first)."""
        params = {
            "api_key": self.api_key,
            "frequency": commodity.frequency,
            "data[0]": "value",
            "facets[series][]": commodity.series_id,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": 0,
            "length": length,
        }
        if start_date:
            params["start"] = start_date
        if end_date:
            params["end"] = end_date
        return params

    def _get(self, url: str, params: dict) -> dict:
        """GET with retries, backoff, and explicit error classification.

        Retries transient failures (timeouts, connection drops, 429, 5xx) up
        to ``max_retries`` times; raises ``EIAAPIError`` on non-retryable
        responses or once attempts are exhausted.
        """
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                # Network-level problem: transient, so retry.
                last_exc = exc
                logger.warning(
                    "Network error on attempt %d/%d: %s",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
            else:
                status = response.status_code
                if status == 200:
                    return self._parse_json(response)

                if status in _RETRYABLE_STATUS:
                    last_exc = EIAAPIError(f"HTTP {status} from EIA API")
                    logger.warning(
                        "Retryable HTTP %d on attempt %d/%d",
                        status,
                        attempt + 1,
                        self.max_retries + 1,
                    )
                    self._maybe_honor_retry_after(response)
                else:
                    # Non-retryable client error (400/403/404/...). Stop now.
                    raise EIAAPIError(
                        f"Non-retryable HTTP {status} from EIA API: "
                        f"{response.text[:200]}"
                    )

            # If we get here, this attempt failed in a retryable way.
            if attempt < self.max_retries:
                self._sleep_backoff(attempt)

        raise EIAAPIError(
            f"EIA request failed after {self.max_retries + 1} attempts: {last_exc}"
        ) from last_exc

    @staticmethod
    def _parse_json(response: requests.Response) -> dict:
        """Parse a 200 response body as JSON, or raise EIAAPIError."""
        try:
            return response.json()
        except ValueError as exc:  # requests raises ValueError on bad JSON
            raise EIAAPIError("EIA API returned malformed JSON") from exc

    def _sleep_backoff(self, attempt: int) -> None:
        """Sleep for exponential backoff plus jitter before the next attempt.

        Jitter (randomized delay) prevents a "thundering herd" where many
        clients retry in lockstep and hammer the server at the same instant.
        """
        delay = self.backoff_factor * (2**attempt)
        delay += random.uniform(0, self.backoff_factor)
        logger.debug("Backing off %.2fs before retry", delay)
        time.sleep(delay)

    @staticmethod
    def _maybe_honor_retry_after(response: requests.Response) -> None:
        """If the server sent a Retry-After header (common on 429), respect it."""
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            wait = int(retry_after)
            logger.info("Honoring Retry-After: sleeping %ds", wait)
            time.sleep(wait)


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys
    from config import COMMODITIES

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings.validate()
    client = EIAClient(api_key=settings.eia_api_key)
    commodity = COMMODITIES[settings.default_commodity]
    data = client.fetch_series(commodity, start_date="2025-01-01")
    rows = data.get("response", {}).get("data", [])
    print(f"\nFetched {len(rows)} rows for {commodity.label}. Latest 3:")
    for row in rows[-3:]:
        print(f"  {row['period']}  {row['value']} {row.get('units', '')}")
    sys.exit(0)
