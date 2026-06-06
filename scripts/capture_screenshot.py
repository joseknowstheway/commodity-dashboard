"""Capture a full-page screenshot of the running dashboard.

Generates `assets/dashboard.png` (used in the README). Requires the database to
be populated (`python run_pipeline.py --refresh`) and Playwright installed:

    pip install playwright
    python -m playwright install chromium
    python scripts/capture_screenshot.py

The script starts the Dash app on a temporary port in a background thread,
renders it in headless Chromium, waits for the Plotly charts to draw, and saves
the screenshot.
"""

from __future__ import annotations

import threading
import time

from playwright.sync_api import sync_playwright

from dashboard.app import app

PORT = 8055
OUTPUT = "assets/dashboard.png"


def _serve() -> None:
    app.run(port=PORT, debug=False, use_reloader=False)


def main() -> None:
    """Serve the app, render it headless, and save the screenshot."""
    server = threading.Thread(target=_serve, daemon=True)
    server.start()
    time.sleep(4)  # give the dev server time to bind

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1440, "height": 1024}, device_scale_factor=2
        )
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        page.wait_for_selector("#price-chart .main-svg", timeout=20000)
        page.wait_for_selector("#zscore-chart .main-svg", timeout=20000)
        time.sleep(1.5)  # let fonts/transitions settle
        page.screenshot(path=OUTPUT, full_page=True)
        browser.close()
    print(f"Saved {OUTPUT}")


if __name__ == "__main__":
    main()
