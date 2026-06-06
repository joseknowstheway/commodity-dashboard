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

## Quick reference

| Goal | Command |
|---|---|
| End-to-end works? | `python -m ingestion.eia_client` |
| Config/key right? | `python -c "from config import settings; settings.validate(); print('OK')"` |
| Code clean? | `black --check . && flake8 .` |
