"""Dash application entry point.

Wires the layout (Chunk 7 layout) and callbacks together over the storage
repository, and exposes the app for both local running and WSGI deployment.

Run locally:
    python -m dashboard.app

Deploy (gunicorn):
    gunicorn dashboard.app:server
"""

from __future__ import annotations

import logging

from dash import Dash

from config import COMMODITIES, settings
from dashboard.callbacks import register_callbacks
from dashboard.layout import build_layout
from storage.repository import CommodityRepository

logger = logging.getLogger(__name__)


def _date_bounds(repo: CommodityRepository, default_key: str):
    """Determine the date-picker bounds from the default commodity's data."""
    series_id = COMMODITIES[default_key].series_id
    df = repo.get_processed(series_id)
    if df.empty:
        return None, None
    dates = df["date"].dt.strftime("%Y-%m-%d")
    return dates.min(), dates.max()


def create_app() -> tuple[Dash, CommodityRepository]:
    """Build the Dash app, repository, layout, and callbacks."""
    repo = CommodityRepository(settings.db_path)
    default_key = settings.default_commodity
    date_min, date_max = _date_bounds(repo, default_key)

    app = Dash(__name__, title="Commodity Price Analytics")
    app.layout = build_layout(COMMODITIES, default_key, date_min, date_max)
    register_callbacks(app, repo)
    logger.info("Dashboard ready (data window %s..%s)", date_min, date_max)
    return app, repo


# Module-level app/server so `python -m dashboard.app` and WSGI servers
# (e.g. gunicorn dashboard.app:server) both work.
app, _repo = create_app()
server = app.server


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app.run(debug=True, port=8050)
