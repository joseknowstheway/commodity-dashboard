# Commodity Price Analytics Dashboard

A production-structured Python application that ingests live U.S. energy
commodity prices from the [EIA public API](https://www.eia.gov/opendata/),
transforms and stores them in a relational database, and serves an interactive
[Plotly Dash](https://dash.plotly.com/) dashboard with trend analysis and
time-series forecasting.

**Commodities tracked:** WTI Crude Oil, Natural Gas (Henry Hub), Regular Gasoline.

> Built to demonstrate Python data engineering, OOP design, SQL, REST API
> integration, data visualization, and testing.

📓 See [`concept_walkthrough.md`](concept_walkthrough.md) for a stage-by-stage
log of the concepts, design decisions, and trade-offs behind the build.

---

## Architecture

```
EIA REST API
     │  (requests, retries, logging)
     ▼
[ ingestion ]  EIAClient ─────► raw JSON
     │
     ▼
[ transform ]  CommodityPipeline ─► clean + enriched DataFrame
     │            (pct_change, rolling avg/std, z-score)
     ▼
[ storage ]    CommodityRepository ─► SQLite (repository pattern)
     │            raw_prices | processed_prices
     ▼
[ analytics ]  PriceForecaster (ARIMA) ─► forecast + CI
     │
     ▼
[ dashboard ]  Plotly Dash ─► interactive charts + KPIs
```

---

## Tech Stack

| Layer        | Tools                              |
|--------------|------------------------------------|
| Ingestion    | `requests`                         |
| Transform    | `pandas`, `numpy`                  |
| Storage      | `sqlite3` (stdlib)                 |
| Forecasting  | `statsmodels`, `scikit-learn`      |
| Dashboard    | `plotly`, `dash`                   |
| Testing      | `pytest`                           |
| Config       | `python-dotenv`                    |

---

## Setup

Requires **Python 3.11+** (developed on 3.14).

```bash
# 1. Clone and enter the project
git clone <your-repo-url> commodity-dashboard
cd commodity-dashboard

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt          # runtime only
# pip install -r requirements-dev.txt    # + pytest, black, flake8

# 4. Configure your environment
cp .env.example .env
# Edit .env and add your free EIA API key from https://www.eia.gov/opendata/
```

---

## Usage

> The pipeline and dashboard are built in later chunks. This section will be
> expanded as those land.

```bash
# Populate the database end-to-end (Chunk 5)
python run_pipeline.py

# Launch the dashboard (Chunk 7)
python -m dashboard.app
```

---

## Configuration

All settings live in `.env` (never committed). See `.env.example` for the
template.

| Variable            | Default              | Description                              |
|---------------------|----------------------|------------------------------------------|
| `EIA_API_KEY`       | _(required)_         | Free key from eia.gov/opendata           |
| `DB_PATH`           | `data/commodity.db`  | SQLite database location                 |
| `DEFAULT_COMMODITY` | `WTI_CRUDE`          | Commodity shown on first load            |
| `LOOKBACK_DAYS`     | `730`                | Days of history to fetch by default      |

---

## Project Status

Built in independent chunks; each leaves the project runnable.

- [x] **Chunk 1** — Project setup & configuration
- [ ] **Chunk 2** — Ingestion layer (EIA API client)
- [ ] **Chunk 3** — Transformation pipeline
- [ ] **Chunk 4** — Storage layer (repository pattern)
- [ ] **Chunk 5** — Orchestration script
- [ ] **Chunk 6** — Forecasting module
- [ ] **Chunk 7** — Plotly Dash dashboard
- [ ] **Chunk 8** — Test suite
- [ ] **Chunk 9** — Polish & documentation

---

## License

This is a personal portfolio project.
