# Commodity Price Analytics Dashboard
### A Python Portfolio Project for Data-Driven Software Engineering Roles

---

## Project Overview

A self-contained, production-structured Python application that ingests live commodity price data from a public REST API, processes and stores it in a relational database, and serves an interactive Plotly Dash dashboard with trend analysis and basic forecasting.

**Domain:** Energy & commodity markets (crude oil, natural gas, gasoline) via the U.S. Energy Information Administration (EIA) public API.

**Why EIA?** Free, no credit card required, well-documented, and directly relevant to Houston's commercial energy sector — a subtle but meaningful signal to hiring managers in this market.

**Stack:**
- `requests` — REST API ingestion
- `pandas`, `numpy` — data transformation and analytics
- `sqlite3` (stdlib) — relational storage via a repository pattern
- `plotly`, `dash` — interactive dashboard
- `statsmodels` — simple time-series forecasting
- `pytest` — unit test suite
- `python-dotenv` — environment/config management
- `Git` — version control with clean commit history

---

## Project Structure

```
commodity-dashboard/
├── README.md
├── requirements.txt
├── .env.example
├── config.py
├── data/
│   └── commodity.db          # SQLite database (git-ignored)
├── ingestion/
│   ├── __init__.py
│   └── eia_client.py         # EIA API client class
├── transform/
│   ├── __init__.py
│   └── pipeline.py           # Pandas transformation pipeline
├── storage/
│   ├── __init__.py
│   └── repository.py         # Repository pattern for DB access
├── analytics/
│   ├── __init__.py
│   └── forecasting.py        # Forecasting and statistical models
├── dashboard/
│   ├── __init__.py
│   ├── app.py                # Dash app entry point
│   ├── layout.py             # Dashboard layout definition
│   └── callbacks.py          # Dash interactivity callbacks
└── tests/
    ├── __init__.py
    ├── test_pipeline.py
    ├── test_repository.py
    └── test_forecasting.py
```

---

## Build Chunks

Work through these in order. Each chunk is independently completable and produces something runnable before moving to the next.

---

### Chunk 1 — Project Setup & Configuration
**Goal:** Establish a clean, professional project skeleton.

**Tasks:**
- Initialize a Git repo with a `.gitignore` (exclude `data/`, `.env`, `__pycache__`)
- Create `requirements.txt` with pinned versions
- Create `config.py` that reads from `.env` using `python-dotenv`
  - Variables: `EIA_API_KEY`, `DB_PATH`, `DEFAULT_COMMODITY`, `LOOKBACK_DAYS`
- Create `.env.example` as a safe template to commit
- Register for a free EIA API key at [eia.gov/opendata](https://www.eia.gov/opendata/)
- Write an initial `README.md` with project purpose, setup instructions, and how to run

**Deliverable:** A repo you can clone fresh and get running in under 5 minutes.

---

### Chunk 2 — Ingestion Layer (EIA API Client)
**Goal:** Build a reusable, OOP client that fetches commodity price data from the EIA API.

**Key EIA series to target:**
| Commodity | EIA Series ID |
|---|---|
| WTI Crude Oil | `PET.RWTC.W` |
| Natural Gas (Henry Hub) | `NG.RNGWHHD.W` |
| Regular Gasoline (US avg) | `PET.EMM_EPMR_PTE_NUS_DPG.W` |

**Tasks:**
- Create `ingestion/eia_client.py` with an `EIAClient` class
  - `__init__` accepts `api_key` and base URL
  - `fetch_series(series_id, start_date, end_date)` — returns raw JSON response
  - `fetch_multiple(series_ids, ...)` — loops and returns a dict keyed by series ID
- Handle HTTP errors explicitly (`requests.HTTPError`, timeouts, rate limits)
- Add retry logic with exponential backoff (use `time.sleep` or `tenacity`)
- Log requests and errors using Python's `logging` module (not `print`)

**Demonstrates:** REST API integration, OOP design, production error handling.

---

### Chunk 3 — Transformation Pipeline
**Goal:** Convert raw API JSON into clean, analytics-ready Pandas DataFrames.

**Tasks:**
- Create `transform/pipeline.py` with a `CommodityPipeline` class
  - `parse_raw(raw_json, series_id)` — extracts date/value pairs into a DataFrame
  - `clean(df)` — handles nulls, type casting, outlier flagging
  - `enrich(df)` — adds calculated columns:
    - `pct_change` — week-over-week % change
    - `rolling_avg_4w` — 4-week rolling mean (NumPy/Pandas)
    - `rolling_std_4w` — 4-week rolling standard deviation
    - `z_score` — standardized price relative to trailing window
  - `run(raw_json, series_id)` — orchestrates parse → clean → enrich
- Keep each method pure (input in, output out) for easy unit testing

**Demonstrates:** Pandas proficiency, NumPy, clean OOP, data engineering thinking.

---

### Chunk 4 — Storage Layer (Repository Pattern)
**Goal:** Persist data to SQLite using a clean repository abstraction — not raw SQL scattered throughout the codebase.

**Schema:**

```sql
-- Raw ingested records
CREATE TABLE raw_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id   TEXT NOT NULL,
    date        DATE NOT NULL,
    value       REAL,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(series_id, date)
);

-- Processed/enriched records
CREATE TABLE processed_prices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id       TEXT NOT NULL,
    date            DATE NOT NULL,
    value           REAL,
    pct_change      REAL,
    rolling_avg_4w  REAL,
    rolling_std_4w  REAL,
    z_score         REAL,
    processed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(series_id, date)
);
```

**Tasks:**
- Create `storage/repository.py` with a `CommodityRepository` class
  - `__init__` opens a connection and calls `_initialize_schema()`
  - `upsert_raw(df, series_id)` — inserts raw records, ignores duplicates
  - `upsert_processed(df, series_id)` — inserts enriched records
  - `get_processed(series_id, start_date, end_date)` — returns a DataFrame
  - `get_latest_date(series_id)` — used to avoid re-fetching existing data
  - Use context managers (`with` statement) for connection handling
- Use `INSERT OR IGNORE` for idempotent upserts

**Demonstrates:** SQL schema design, repository pattern, idempotency, relational DB thinking.

---

### Chunk 5 — Orchestration Script
**Goal:** Wire Chunks 2–4 into a single runnable pipeline script.

**Tasks:**
- Create `run_pipeline.py` at the project root
  - Accepts optional CLI args via `argparse`: `--series`, `--start`, `--end`
  - Instantiates `EIAClient`, `CommodityPipeline`, `CommodityRepository`
  - For each series: fetch → transform → upsert raw → upsert processed
  - Prints a summary: records fetched, records inserted, time elapsed
- Add a `--refresh` flag that forces re-fetch even if data exists

**Deliverable:** Running `python run_pipeline.py` populates the database end-to-end.

---

### Chunk 6 — Forecasting Module
**Goal:** Add a lightweight forecasting layer using `statsmodels`.

**Tasks:**
- Create `analytics/forecasting.py` with a `PriceForecaster` class
  - `fit(df)` — fits a simple ARIMA(1,1,1) model to the price series
  - `predict(steps=8)` — returns a DataFrame with forecasted dates and values, plus 95% confidence interval bounds
  - `summary()` — returns model diagnostics as a dict
- Keep the model simple — the goal is to demonstrate awareness of time-series concepts, not to build a production forecasting system
- Optionally add a `LinearTrendForecaster` using `scikit-learn` as an alternative/comparison

**Demonstrates:** Statsmodels, scikit-learn awareness, forecasting concepts — all preferred qualifications.

---

### Chunk 7 — Plotly Dash Dashboard
**Goal:** Build the interactive visualization layer.

**Dashboard layout:**

```
┌─────────────────────────────────────────────┐
│  COMMODITY PRICE ANALYTICS                  │
│  [Commodity Dropdown]  [Date Range Picker]  │
├───────────────┬─────────────────────────────┤
│  $XX.XX       │  Price History + Forecast   │
│  Current Price│  (line chart with CI band)  │
│               │                             │
│  +X.X% WoW   ├─────────────────────────────┤
│               │  Z-Score (Volatility)       │
│  4W Avg: $XX  │  (bar chart, color-coded)   │
└───────────────┴─────────────────────────────┘
```

**Tasks:**
- `dashboard/layout.py` — defines the static HTML/component structure
  - Dropdown for commodity selection
  - DatePickerRange for filtering
  - KPI cards: current price, WoW change, 4-week average
  - Two charts: price history with forecast overlay, z-score bar chart
- `dashboard/callbacks.py` — Dash callbacks that wire dropdowns/dates to chart updates
  - Query `CommodityRepository` on each interaction
  - Run `PriceForecaster` and overlay forecast on the price chart
- `dashboard/app.py` — initializes the Dash app, imports layout, registers callbacks

**Demonstrates:** Plotly Dash (preferred qual), interactivity, data visualization, UX thinking.

---

### Chunk 8 — Test Suite
**Goal:** Write a meaningful `pytest` suite that covers core logic.

**Tests to write:**

| File | Tests |
|---|---|
| `test_pipeline.py` | `clean()` handles nulls; `enrich()` computes correct rolling avg; `pct_change` is accurate on known data |
| `test_repository.py` | Upsert is idempotent (no duplicate rows); `get_processed` returns correct date range; schema is initialized correctly |
| `test_forecasting.py` | `predict()` returns correct number of steps; confidence intervals are wider than point estimates; model handles short series gracefully |

**Tasks:**
- Use `pytest` fixtures for a temporary in-memory SQLite DB
- Use `pandas.testing.assert_frame_equal` for DataFrame assertions
- Aim for at least 10 meaningful test cases
- Add a `pytest.ini` or `pyproject.toml` config section

**Demonstrates:** Unit testing discipline, CI/CD readiness, production code mindset.

---

### Chunk 9 — Polish & Documentation
**Goal:** Make the repo look like something a professional engineer would hand off.

**Tasks:**
- Flesh out `README.md` with:
  - Architecture diagram (even ASCII is fine)
  - Setup instructions (clone, install, configure `.env`, run pipeline, launch dashboard)
  - Screenshot of the dashboard
  - Design decisions section (why repository pattern, why EIA, why SQLite over Postgres)
- Add docstrings to all public classes and methods (Google style)
- Add type hints throughout (`def fetch_series(self, series_id: str, start_date: str) -> dict`)
- Run `black` for formatting, `flake8` for linting — mention both in README
- Tag `v1.0.0` in Git

---

## Stretch Goals (Optional, High Impact)

These are worth adding if time permits — each maps directly to a preferred qualification:

| Stretch Goal | Qualification it demonstrates |
|---|---|
| Deploy Dash app to AWS EC2 or Elastic Beanstalk | AWS cloud familiarity |
| Store raw data in AWS S3 instead of local files | Cloud-scale data architecture |
| Add a `Makefile` with `make run`, `make test`, `make lint` targets | DevOps/CI awareness |
| GitHub Actions workflow that runs `pytest` on every push | CI/CD pipelines |
| Add a second commodity domain (metals, agriculture) via a different API | Extensibility and design patterns |
| Jupyter notebook with exploratory analysis | Jupyter proficiency |

---

## Interview Talking Points

When asked about this project, be ready to discuss:

- **Why the repository pattern?** Decouples business logic from storage — you could swap SQLite for Postgres with no changes to the pipeline or dashboard.
- **Why ARIMA(1,1,1)?** Commodity prices are non-stationary (need differencing = the middle `1`), and a simple model is more defensible than an overfit one.
- **How would you scale this?** Replace the EIA polling script with an event-driven ingestion trigger (e.g., AWS Lambda + EventBridge on a schedule), and promote SQLite to RDS PostgreSQL.
- **What would you add next?** Alerting (email/Slack when z-score exceeds ±2), user authentication on the dashboard, multi-tenant support.

---

*Built to demonstrate Python data engineering, OOP design, SQL, REST APIs, visualization, and testing — targeting Software Engineer I roles in data-driven commercial environments.*
