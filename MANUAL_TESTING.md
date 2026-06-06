# Manual Testing Guide

How to manually exercise the project by hand — what to run, and what a correct
result looks like. Complements the automated `pytest` suite (Chunk 8) and the
concept notes in [`concept_walkthrough.md`](concept_walkthrough.md).

> This document grows one section per build chunk.

---

## 0. One-time setup (every terminal session)

The project path contains spaces, so quote it. Activating the virtualenv lets
you type `python` instead of the full `.venv/bin/python`.

```bash
cd "/Users/princetaylor/Desktop/ClaudeHub/JobHunt/Software Engineer/Projects/commodity-dashboard"
source .venv/bin/activate
```

Your prompt should now show `(.venv)`. Verify the interpreter:

```bash
which python          # -> .../commodity-dashboard/.venv/bin/python
python --version      # Python 3.14.3
```

Leave the venv later with `deactivate`.

> Tip: inside Claude Code you can also run any command by prefixing it with `!`
> (e.g. `! python -m ingestion.eia_client`) to execute it in the session.

---

## Always-available quality checks

These apply at every stage — run them after any change.

```bash
black --check .       # "All done!" = formatting clean
flake8 .              # no output = no lint errors
```

Confirm secrets stay out of git:

```bash
git check-ignore -v .env    # prints a .gitignore rule = .env IS ignored
git status                  # .env must NOT appear here
```

---

## Chunk 1 — Configuration

**1a. Settings load and the API key is picked up from `.env`:**
```bash
python -c "from config import settings; print('key set?', bool(settings.eia_api_key)); print('db:', settings.db_path); print('lookback:', settings.lookback_days)"
```
Expect `key set? True`.

**1b. `validate()` passes with a real key:**
```bash
python -c "from config import settings; settings.validate(); print('validate() OK')"
```

**1c. Fail-fast: validation raises when the key is missing.** (Overrides the env
var to empty for this one command — proves real env vars beat `.env`.)
```bash
EIA_API_KEY="" python -c "from config import settings; settings.validate()"
```
Expect a `ValueError` telling you to set `EIA_API_KEY`.

**1d. Inspect the commodity catalog:**
```bash
python -c "from config import COMMODITIES; [print(c.key, '->', c.route, c.series_id, c.unit) for c in COMMODITIES.values()]"
```
Shows 3 commodities mapped to their EIA v2 routes/facets.

---

## Chunk 2 — Ingestion (EIA API client)

### Fast path — built-in smoke test
The client has a `__main__` block; this is the quickest end-to-end check (it
hits the **live** EIA API):
```bash
python -m ingestion.eia_client
```
Expect `Fetching WTI_CRUDE...` / `Fetched N rows...` logs and the latest 3
prices. Proves config → client → live network call → parsed response.

### Interactive exploration (REPL)
Start `python`, then:
```python
import logging
logging.basicConfig(level=logging.INFO)          # show client logs
from config import settings, COMMODITIES
from ingestion.eia_client import EIAClient

client = EIAClient(api_key=settings.eia_api_key)

raw = client.fetch_series(COMMODITIES["WTI_CRUDE"], start_date="2026-01-01")
rows = raw["response"]["data"]
print("rows:", len(rows))
print("first:", rows[0])
print("last :", rows[-1])
```
Note `value` arrives as a **string** and `period` as a string date — Chunk 3's
pipeline owns the type-casting.

Other commodities (just change the key):
```python
print(client.fetch_series(COMMODITIES["NATURAL_GAS"], start_date="2026-01-01")["response"]["data"][-1])
print(client.fetch_series(COMMODITIES["GASOLINE"], start_date="2026-01-01")["response"]["data"][-1])
```

`fetch_multiple` (all three, keyed by series_id):
```python
out = client.fetch_multiple(COMMODITIES.values(), start_date="2026-01-01")
for sid, payload in out.items():
    print(sid, "->", len(payload["response"]["data"]), "rows")
```

Date window (proves `start`/`end` params):
```python
w = client.fetch_series(COMMODITIES["WTI_CRUDE"], start_date="2025-06-01", end_date="2025-06-30")
print([r["period"] for r in w["response"]["data"]])   # only June 2025
```

### Error handling
**Bad key → fails fast (403, non-retryable, no backoff delay):**
```python
from ingestion.eia_client import EIAClient, EIAAPIError
bad = EIAClient(api_key="totally-invalid-key")
try:
    bad.fetch_series(COMMODITIES["WTI_CRUDE"])
except EIAAPIError as e:
    print("Caught as expected:", str(e)[:120])
```

**Empty key rejected at construction:**
```python
try:
    EIAClient(api_key="")
except ValueError as e:
    print("Rejected:", e)
```

**Retry/backoff without the network** (fake session, sleeps patched out):
```python
from unittest.mock import MagicMock, patch
from ingestion.eia_client import EIAClient
from config import COMMODITIES

def fake(status, data=None):
    r = MagicMock(); r.status_code = status; r.headers = {}
    r.json.return_value = data or {"response": {"data": [], "total": 0}}
    r.text = "err"; return r

with patch("ingestion.eia_client.time.sleep"):
    s = MagicMock()
    s.get.side_effect = [fake(503), fake(503), fake(200)]   # fail twice, then succeed
    c = EIAClient(api_key="x", session=s)
    c.fetch_series(COMMODITIES["WTI_CRUDE"])
    print("calls =", s.get.call_count)     # -> 3 (retried twice). Use 404 -> 1 (fail fast)
```

---

## Chunk 3 — Transformation pipeline

### Fast path — live smoke test
Fetch + transform the default commodity and print the enriched tail:
```bash
python -m transform.pipeline
```
Expect a table with `value, is_outlier, pct_change, rolling_avg_4w,
rolling_std_4w, z_score`. The first 3 rolling values are NaN (warm-up).

### Verify the math on known numbers (REPL)
```python
import pandas as pd
from transform.pipeline import CommodityPipeline
p = CommodityPipeline()

# Four values, each +10%
df = pd.DataFrame({"series_id":"T",
                   "date":pd.date_range("2024-01-01",periods=4,freq="W"),
                   "value":[100.0,110.0,121.0,133.1]})
e = p.enrich(p.clean(df))
print(e[["value","pct_change","rolling_avg_4w","z_score"]])
# pct_change -> [NaN, 10, 10, 10];  rolling_avg_4w last -> 116.025
```

### Verify outlier detection is *local* (the Hampel filter)
```python
import numpy as np, pandas as pd
from transform.pipeline import CommodityPipeline
p = CommodityPipeline()

# Smooth trend + ONE injected spike at index 20 -> flags exactly [20]
vals = list(np.linspace(50,70,40)); vals[20] = 200.0
df = pd.DataFrame({"series_id":"T","date":pd.date_range("2024-01-01",periods=40,freq="W"),"value":vals})
print("spike flagged at:", list(p.clean(df).index[p.clean(df)["is_outlier"]]))   # [20]

# Sustained regime shift (50s -> 100s) -> flags 0 (a global detector would flag ~7)
vals3 = [50,51,49,50,52,48,51]+[100,101,99,100,102,98,101]
df3 = pd.DataFrame({"series_id":"T","date":pd.date_range("2024-01-01",periods=14,freq="W"),"value":vals3})
print("regime-shift outliers:", int(p.clean(df3)["is_outlier"].sum()))           # 0
```

### Verify defensive handling (bad values, dupes, empty)
```python
from transform.pipeline import CommodityPipeline
p = CommodityPipeline()
raw = {"response":{"data":[
    {"period":"2024-01-07","value":"100.0"},
    {"period":"2024-01-14","value":"bad"},      # unparseable -> dropped
    {"period":"2024-01-21","value":"120.0"},
    {"period":"2024-01-07","value":"105.0"},     # duplicate date -> keep last
]}}
r = p.clean(p.parse_raw(raw,"T"))
print(len(r), list(r["date"].dt.date.astype(str)), list(r["value"]))  # 2 rows; 01-07=105.0, 01-21=120.0

print(p.run({}, "T").shape)   # (0, 8) -> empty but correctly-shaped, never crashes
```

## Chunk 4 — Storage (repository pattern)

### Fast path — live smoke test (in-memory DB)
Runs fetch -> transform -> store -> read, and shows idempotency:
```bash
python -m storage.repository
```
Expect: `raw inserted: 74`, then on the second write `raw inserted: 0`
(idempotent), and a `get_processed` table at the end.

### Verify the core guarantees (REPL)
```python
import numpy as np, pandas as pd
from storage.repository import CommodityRepository
repo = CommodityRepository(":memory:")

df = pd.DataFrame({"series_id":"T","date":pd.date_range("2024-01-07",periods=5,freq="W"),
    "value":[100.,110.,121.,133.1,146.4],
    "pct_change":[np.nan,10,10,10,10],
    "rolling_avg_4w":[np.nan,np.nan,np.nan,116.025,127.6],
    "rolling_std_4w":[np.nan,np.nan,np.nan,14.25,15.6],
    "z_score":[np.nan,np.nan,np.nan,1.198,1.2],
    "is_outlier":[False,False,True,False,False]})
repo.upsert_processed(df,"T")

# NaN -> NULL for warm-up rows
print(repo._conn.execute("SELECT pct_change FROM processed_prices WHERE date='2024-01-07'").fetchone()[0])  # None

# Real upsert: rewrite with changed values -> overwrites, no dupes
df2 = df.copy(); df2["value"] += 1000
repo.upsert_processed(df2,"T")
got = repo.get_processed("T")
print(len(got), got["value"].iloc[0])      # 5  1100.0  (updated, not duplicated)

# Date-range read + latest date
print(len(repo.get_processed("T", start_date="2024-01-21")))   # 3
print(repo.get_latest_date("T"))                                # 2024-02-04
```

### Inspect the real database file (after running Chunk 5's pipeline)
```bash
sqlite3 data/commodity.db ".tables"
sqlite3 data/commodity.db "SELECT series_id, COUNT(*) FROM processed_prices GROUP BY series_id;"
sqlite3 data/commodity.db "SELECT * FROM processed_prices ORDER BY date DESC LIMIT 5;"
```

## Chunk 5 — Orchestration (run_pipeline.py)

This is the command that creates and populates the real `data/commodity.db`.

### Full run (first time / force refresh)
```bash
python run_pipeline.py --refresh
```
Expect a summary table with ~104 rows per commodity under FETCHED / NEW RAW /
PROCESSED, all status `ok`.

### Incremental run (idempotency)
```bash
python run_pipeline.py
```
Run it again immediately: expect NEW RAW = 0 and status `up-to-date` for every
series (it only fetches the small overlap, inserts nothing, skips recompute).

### Useful flags
```bash
python run_pipeline.py --series WTI_CRUDE                       # one commodity
python run_pipeline.py --series WTI_CRUDE NATURAL_GAS           # a subset
python run_pipeline.py --start 2024-01-01 --end 2024-12-31      # explicit window
python run_pipeline.py --series BOGUS                           # -> argparse error
python run_pipeline.py --help                                  # full CLI help
```

### Inspect the resulting database
```bash
sqlite3 data/commodity.db ".tables"
sqlite3 data/commodity.db "SELECT series_id, COUNT(*), MIN(date), MAX(date) FROM raw_prices GROUP BY series_id;"
# warm-up rows should have NULL rolling stats; later rows populated:
sqlite3 -header -column data/commodity.db "SELECT date, value, rolling_avg_4w, z_score FROM processed_prices WHERE series_id='RWTC' ORDER BY date LIMIT 5;"
```

## Chunk 6 — Forecasting (ARIMA)

> Requires the database to be populated first (`python run_pipeline.py --refresh`).

### Fast path — live smoke test
```bash
python -m analytics.forecasting
```
Expect an ARIMA summary (AIC/BIC/params), an 8-step forecast table with
`lower < forecast < upper`, and a linear-trend comparison.

### Verify the forecast contract (REPL)
```python
import numpy as np, pandas as pd
from analytics.forecasting import PriceForecaster, NotFittedError

dates = pd.date_range("2024-01-07", periods=60, freq="W")
vals  = 50 + 0.5*np.arange(60) + np.random.default_rng(0).normal(0,1,60)
df = pd.DataFrame({"date":dates, "value":vals})

f = PriceForecaster().fit(df)
fc = f.predict(steps=8)
print(len(fc))                                   # 8
print(((fc.lower < fc.forecast) & (fc.forecast < fc.upper)).all())   # True
print((fc.upper - fc.lower).tolist())            # widths grow with horizon
print(f.summary())                               # model/order/n_obs/aic/bic/params

# Graceful failure modes
try: PriceForecaster().fit(df.head(5))
except ValueError as e: print("short series ->", e)
try: PriceForecaster().predict()
except NotFittedError as e: print("no fit ->", e)
```

### Forecast a real commodity from the DB
```python
from storage.repository import CommodityRepository
from analytics.forecasting import PriceForecaster
from config import settings, COMMODITIES
with CommodityRepository(settings.db_path) as repo:
    df = repo.get_processed(COMMODITIES["WTI_CRUDE"].series_id)
print(PriceForecaster().fit(df).predict(steps=8))
```

## Chunk 7 — Dashboard (Plotly Dash)

> Requires a populated DB (`python run_pipeline.py --refresh`).

### Launch it
```bash
python -m dashboard.app
```
Open http://127.0.0.1:8050 — pick a commodity, drag the date range, and watch the
KPI cards, price+forecast chart (with CI band), and z-score bars update.

### Verify the render layer without a browser (REPL)
```python
from dashboard.app import create_app
from dashboard.callbacks import render
app, repo = create_app()
price_fig, z_fig, cur, wow, avg = render(repo, "WTI_CRUDE", None, None)
print(cur, wow.children, avg)                       # KPI strings
print([t.name for t in price_fig.data])             # ['Price','Outlier','95% CI','Forecast']
print(len(render(repo, "WTI_CRUDE", "2026-01-01", "2026-06-30")[0].data[0].x))  # fewer points
repo.close()
```

### Confirm the HTTP layer (server must be running on :8050)
```bash
curl -s -o /dev/null -w "root: %{http_code}\n" http://127.0.0.1:8050/
curl -s -o /dev/null -w "deps: %{http_code}\n" http://127.0.0.1:8050/_dash-dependencies
```
Both should return 200.

## Quick reference

| Goal | Command |
|---|---|
| End-to-end works? | `python -m ingestion.eia_client` |
| Config/key right? | `python -c "from config import settings; settings.validate(); print('OK')"` |
| Code clean? | `black --check . && flake8 .` |
