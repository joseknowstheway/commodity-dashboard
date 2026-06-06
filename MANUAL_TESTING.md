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

## Quick reference

| Goal | Command |
|---|---|
| End-to-end works? | `python -m ingestion.eia_client` |
| Config/key right? | `python -c "from config import settings; settings.validate(); print('OK')"` |
| Code clean? | `black --check . && flake8 .` |
