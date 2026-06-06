# Concept Walkthrough

A running log of the **concepts** behind each stage of this build — written so I
can *explain every line* in an interview, not just ship code.

Each section covers:
- **What was built** — the concrete deliverables.
- **Key ideas** — the concepts and the *why* behind each decision.
- **Mistakes & fixes** — bugs hit during the build and how they were resolved.
- **Interview Q&A** — questions a Cisco-style interviewer is likely to ask, with answers.

> This document grows one section per stage.

---

## Chunk 1 — Project Setup & Configuration

### What was built
- A clean package skeleton: `ingestion/`, `transform/`, `storage/`, `analytics/`,
  `dashboard/`, `tests/` — each a Python package (has `__init__.py`).
- `config.py` — a `python-dotenv`-backed configuration module exposing:
  - an immutable `Settings` dataclass (resolved from environment variables), and
  - a `COMMODITIES` catalog mapping internal keys → EIA series metadata.
- Dependency management: `requirements.txt` (runtime) and `requirements-dev.txt`
  (pytest, black, flake8), with **pinned versions verified to install on Python 3.14**.
- `.env.example` (committed template) + `.env` (real secrets, git-ignored).
- Tooling config: `pyproject.toml` (black + pytest) and `.flake8` (linter).
- `.gitignore` that excludes secrets, the database, the virtualenv, and caches.
- `README.md` with architecture diagram, setup steps, and a chunk roadmap.

### Key ideas

**1. Separation of configuration from code (12-Factor App).**
Secrets and environment-specific values never live in source. They're read from
the environment at runtime. `.env` holds them locally and is git-ignored;
`.env.example` documents the required keys without leaking values. This is what
lets the same code run unchanged on a laptop, in CI, and in production.

**2. `load_dotenv(override=False)` — precedence matters.**
Real environment variables take priority over the `.env` file. In CI/production
you inject env vars directly, and they *should* beat a local file. Getting this
backwards is a classic "works on my machine" bug.

**3. Immutable config via `@dataclass(frozen=True)`.**
Once `Settings` is built it can't be mutated. Config that silently changes
mid-run is a nasty source of bugs; freezing it makes the program's
configuration a fixed, known quantity. Bonus: type hints give autocomplete and
catch typos.

**4. Validation is separate from loading (`validate()` is its own method).**
Importing `config` never fails — so unit tests can import it without a real API
key. Only entry points that actually call the API invoke `validate()`, which
fails *loudly and early* with an actionable message. Principle: **fail fast, but
only where the missing thing actually matters.**

**5. Single source of truth (`COMMODITIES`).**
EIA series IDs are defined in exactly one place. Ingestion, storage, and the
dashboard dropdown all read from this catalog. Adding a commodity is a
one-line change that propagates everywhere — no hunting for hardcoded strings.

**6. Pinned, verified dependencies = reproducible builds.**
Versions weren't guessed; they were installed into a venv and frozen from what
actually resolved on Python 3.14. Pinning means a clone six months from now
builds identically. Splitting runtime vs dev deps keeps production installs lean.

### Mistakes & fixes
- **`data/.gitkeep` was being ignored.** The first `.gitignore` had `data/`,
  which ignores the *entire directory* including the `.gitkeep` placeholder — so
  a fresh clone wouldn't have the `data/` folder the DB path expects.
  **Fix:** changed to `data/*` + `!data/.gitkeep` (ignore contents, keep the
  directory).
- **flake8 ignored `pyproject.toml`.** I initially put all tool config in
  `pyproject.toml`, but flake8 doesn't read it. flake8 silently used defaults.
  **Fix:** added a dedicated `.flake8` file with `max-line-length = 88` and
  `extend-ignore = E203, W503` (the two rules that conflict with black).
- **black reformatted `config.py` on first pass.** My inline-comment spacing
  didn't match black's style. **Fix:** ran `black .` and adopted its formatting
  as the baseline; the code now passes `black --check` and `flake8` cleanly.

### Interview Q&A

**Q: How do you keep secrets out of source control?**
A: Secrets live in a git-ignored `.env`, loaded at runtime via `python-dotenv`.
I commit a `.env.example` template so the required keys are documented without
exposing values, and I verified with `git check-ignore` that `.env` is actually
ignored before the first push. I also confirmed the pushed repo on GitHub
contained only `.env.example`, not `.env`.

**Q: Why a frozen dataclass for settings instead of a dict or module constants?**
A: Type safety, immutability, and a single validation point. A dict has no
schema and can be mutated anywhere; bare constants can't carry defaults or
validate. The frozen dataclass gives me autocomplete, prevents accidental
mutation, and gives one obvious place — `validate()` — to enforce invariants.

**Q: Why separate `validate()` from loading the config?**
A: So that importing the config module never fails. Tests and tooling can import
it without a live API key. Validation runs only at the entry points that need
the key, where failing fast with a clear message is actually helpful.

**Q: Why pin dependency versions, and why split runtime vs dev?**
A: Pinning makes builds reproducible — the same versions resolve every time,
avoiding "it broke after an upstream release." Splitting runtime from dev
(pytest/black/flake8) keeps production environments minimal: you don't ship test
and lint tooling to prod.

**Q: What does the repository's package structure buy you?**
A: Each layer (ingestion, transform, storage, analytics, dashboard) is an
isolated package with a single responsibility. That separation makes each piece
independently testable and swappable — e.g. I can replace the storage backend
without touching ingestion. It mirrors how the data actually flows through the
system.

**Q: Walk me through what happens when `config.py` is imported.**
A: It resolves `PROJECT_ROOT`, calls `load_dotenv` to pull `.env` into the
environment (without overriding real env vars), then `_load_settings()` reads
each variable with a default, resolves `DB_PATH` to an absolute path, coerces
`LOOKBACK_DAYS` to an int (raising a clear error if it's not), and builds the
frozen `Settings` singleton. Validation is *not* run on import — that's deferred
to `validate()`.

---

## Chunk 2 — Ingestion Layer (EIA API Client)

### What was built
- `ingestion/eia_client.py` containing:
  - `EIAClient` — an OOP client for the **EIA v2** REST API.
  - `EIAAPIError` — one project-specific exception that wraps all failure modes.
- Public methods:
  - `fetch_series(commodity, start_date, end_date)` — fetches one series, returns raw JSON.
  - `fetch_multiple(commodities, ...)` — fetches many, keyed by `series_id`, skipping failures.
- Internals: `_build_params` (v2 query shaping), `_get` (retry loop + error
  classification), `_parse_json`, `_sleep_backoff`, `_maybe_honor_retry_after`.
- Extended the `COMMODITIES` catalog in `config.py` to carry the v2 `route` and
  `series_id` facet code (+ `frequency`), verified against the live API.
- A `__main__` smoke test so `python -m ingestion.eia_client` fetches the
  default commodity and prints the latest rows.

### Key ideas

**1. v1 vs v2 API — read the docs, don't trust the spec.**
The project brief listed legacy **v1** series IDs (`PET.RWTC.W`). The current
EIA API is **v2**, which addresses data by a *route* (`petroleum/pri/spt`) plus a
*series facet* (`RWTC`) and a *frequency*. I probed the live v2 endpoints to
confirm the correct route/facet for each commodity before writing a line of the
client. Interview-ready point: *I used the current API version, not a deprecated
one — and I verified the contract empirically.*

**2. Retry only what's worth retrying (error classification).**
Failures fall into two buckets. **Transient** — timeouts, dropped connections,
`429` (rate limited), `5xx` (server problems) — are retried. **Permanent** —
other `4xx` like `400/403/404` — are raised *immediately*, because retrying a
malformed or unauthorized request just wastes time and hammers the server. This
distinction is the heart of good retry logic.

**3. Exponential backoff + jitter.**
The delay before retry *n* is `backoff_factor * 2**n` (0.5s, 1s, 2s, 4s…), plus a
small random jitter. Exponential growth gives a struggling server room to
recover; **jitter** prevents the "thundering herd" problem where many clients
retry in lockstep and re-spike the load at the same instant. We also honor a
`Retry-After` header if the server sends one (common with `429`).

**4. Always set a timeout.**
Every request passes `timeout=`. Without it, a hung server can block the client
*forever* — one of the most common production outages. A timeout converts "hang
forever" into a catchable, retryable error.

**5. One Session for connection pooling.**
The client holds a `requests.Session`, which keeps TCP connections alive
(keep-alive) and reuses them across requests instead of paying the
connect/TLS-handshake cost every call. The session is also injectable, which
makes the client trivial to unit-test with a mock.

**6. Wrap failures in one exception type.**
Callers catch `EIAAPIError`, not a grab-bag of `requests.Timeout`,
`ConnectionError`, `HTTPError`, and `ValueError`. This is the **exception
translation** pattern: the ingestion layer's failure contract is one type, so
upstream code (pipeline, orchestrator) stays clean.

**7. Single responsibility.**
The client *fetches and returns raw JSON* — it does not parse into DataFrames or
touch the database. Parsing is the transform layer's job (Chunk 3). Keeping the
client narrow makes each layer independently testable.

**8. Partial failure tolerance in `fetch_multiple`.**
One bad series is logged and skipped rather than aborting the whole batch — a
single failing commodity can't sink an entire pipeline run.

### Mistakes & fixes
- **The spec's series IDs didn't work.** v1 IDs like `PET.RWTC.W` return nothing
  on the v2 API. **Fix:** probed `https://api.eia.gov/v2/...` directly to find
  the right `route` + `series` facet per commodity, then redesigned the
  `Commodity` dataclass around v2 (route/facet/frequency).
- **Deviated from the spec's method signature.** The brief said
  `fetch_series(series_id, ...)`, but v2 needs *route + facet*, not a single ID.
  **Fix:** `fetch_series` takes a `Commodity` object (the single source of
  truth), which is cleaner than threading two strings around — a deliberate,
  defensible deviation.
- **black flagged a long log line (E501, 93 > 88).** **Fix:** ran `black`, which
  wrapped the `logger.info(...)` call across lines.

### Interview Q&A

**Q: Which errors do you retry, and which do you not? Why?**
A: I retry *transient* failures — network timeouts, connection drops, HTTP 429,
and 5xx — because they're likely to succeed on a second attempt. I do **not**
retry other 4xx (400/403/404): those mean the request itself is wrong
(bad params, bad key, missing resource), so retrying can't help and just adds
load. I classify on status code in `_get` and raise `EIAAPIError` immediately for
the non-retryable bucket.

**Q: What is jitter and why add it to backoff?**
A: Jitter is a small random amount added to each backoff delay. Without it, many
clients that failed at the same moment would retry at the exact same future
instants — a "thundering herd" that re-overloads the server in synchronized
waves. Randomizing the delays spreads the retries out.

**Q: Why pass a timeout to every request?**
A: Because TCP has no built-in deadline — a stalled server can leave a request
hanging indefinitely, tying up a thread/connection forever. A timeout turns that
into a `requests.Timeout` I can catch and retry. Omitting timeouts is a classic
cause of cascading production hangs.

**Q: Why reuse a single `requests.Session`?**
A: Connection pooling. A Session keeps TCP/TLS connections alive and reuses them
across requests, avoiding a fresh handshake every call — meaningfully faster for
multiple fetches. It also gives me a clean injection point for testing.

**Q: How did you test the retry logic without waiting on real backoff or
hitting the network?**
A: I injected a `MagicMock` session with scripted responses (e.g. `[503, 503,
200]`) and patched `time.sleep`, then asserted on `session.get.call_count` and
the raised exception. That verifies retry-then-succeed, immediate raise on 404,
retry exhaustion, and timeout handling — all in milliseconds, deterministically.

**Q: Why wrap everything in `EIAAPIError` instead of letting requests'
exceptions propagate?**
A: Exception translation. Callers shouldn't need to know the ingestion layer
uses `requests` — that's an implementation detail. Exposing one failure type
keeps the boundary clean and means I could swap `requests` for `httpx` later
without changing any caller's `except` clause.

**Q: How would you handle a series with more rows than one request returns?**
A: The v2 API caps a page at 5000 rows and reports `total`. Today our weekly
windows are ~100 rows so one request suffices, and I log a warning if `total`
ever exceeds the requested `length`. To scale, I'd loop on the `offset`
parameter, accumulating pages until I've fetched `total` rows.

---

## Chunk 3 — Transformation Pipeline

### What was built
- `transform/pipeline.py` with `CommodityPipeline`, a **stateless** transformer:
  - `parse_raw(raw_json, series_id)` — pulls `(date, value)` out of the EIA
    payload into a `[series_id, date, value]` DataFrame.
  - `clean(df)` — casts types, drops nulls, sorts, de-duplicates by date, and
    adds a robust `is_outlier` flag.
  - `enrich(df)` — adds `pct_change`, `rolling_avg_4w`, `rolling_std_4w`,
    `z_score`.
  - `run(raw_json, series_id)` — composes the three.
- A `__main__` smoke test that fetches live and prints the enriched tail.

### Key ideas

**1. Pure functions = testable functions.**
Every method takes input and returns a *new* DataFrame (`df.copy()`), never
mutating its argument or shared state. Purity means each step can be unit-tested
with a hand-built DataFrame and a known expected output — no network, no DB, no
ordering dependencies. This is the single biggest reason the pipeline is split
into `parse_raw` / `clean` / `enrich` instead of one big function.

**2. Separation of parsing from cleaning.**
`parse_raw` deliberately leaves `value`/`date` as raw strings; `clean` owns all
type-casting (`pd.to_numeric(..., errors="coerce")`, `pd.to_datetime`). One
responsibility per method.

**3. Coerce, then drop — graceful handling of bad data.**
`errors="coerce"` turns unparseable values into `NaN` instead of throwing, then
`dropna` removes them. A single malformed row can't crash the run.

**4. Idempotent, ordered output.**
`clean` sorts ascending by date and drops duplicate `(series_id, date)` pairs
keeping the last — so re-running on overlapping data yields a stable, correctly
ordered series (which the rolling windows depend on).

**5. Rolling analytics and the NaN "warm-up".**
`rolling(window=4)` means the first 3 rows are `NaN` — there isn't enough history
to compute a 4-week stat. That's correct, not a bug; downstream code (storage,
charts) must tolerate leading NaNs. `z_score` standardizes each price against
its trailing window: `(value - rolling_mean) / rolling_std`.

**6. Empty input returns an empty *correctly-shaped* frame.**
`run({})` yields 0 rows but all 8 columns. Downstream code can always assume the
schema exists and never special-cases `None`.

### Mistakes & fixes
- **Outlier detector flagged an entire price *regime*, not anomalies.** My first
  `_flag_outliers` used a **global** median/MAD modified z-score. On real data it
  flagged 12/74 WTI points — the whole recent high-price stretch — because the
  global median sat down at the old price level (~$65) while prices had trended
  up to ~$100. A global robust statistic still can't tell "new sustained level"
  from "anomaly" on a trending series.
  **Fix:** switched to a **Hampel filter** — a *centered rolling* median/MAD, so
  each point is judged against its **local neighbors**. I verified the fix with
  controlled cases: a smooth trend with one injected spike flags *exactly* the
  spike; a pure trend flags nothing; a sustained regime shift flags nothing
  (the global version would have flagged ~7). Lesson: for time series, outlier
  detection must be **local**.
- **`black` reflowed long log/lines and the constants block.** Ran `black`;
  adopted its formatting.

### Interview Q&A

**Q: Why split the transform into parse/clean/enrich instead of one function?**
A: Single responsibility and testability. Each method is pure — input in, new
DataFrame out — so I can unit-test `enrich`'s math on a 4-row hand-built frame
without any API or DB. It also makes failures localizable: if a number's wrong,
I know which stage to look at.

**Q: How do you handle a bad/non-numeric value from the API?**
A: `pd.to_numeric(..., errors="coerce")` turns it into `NaN` rather than raising,
then `dropna(subset=["date","value"])` removes it and I log the drop count.
Defensive parsing — one bad row never aborts the batch.

**Q: Why are the first few rows of the rolling columns NaN?**
A: A 4-week rolling stat needs 4 observations; the first 3 rows don't have
enough history, so pandas returns NaN. It's mathematically correct. I make sure
storage and the charts tolerate leading NaNs.

**Q: Walk me through your outlier detection — and why MAD instead of mean/std?**
A: I use a Hampel filter: a centered rolling window, flagging points more than
3.5 robust sigmas from the local median, scaled by the window's MAD (×1.4826).
MAD (median absolute deviation) is robust — unlike mean/std it isn't dragged
around by the very outliers it's trying to detect. And it's *local* (rolling),
because commodity prices trend; a global detector would flag an entire sustained
price move as outliers. I confirmed that on real data and switched approaches.

**Q: Your outliers are flagged but not removed — why?**
A: Removing real (if extreme) market moves would distort the analytics and the
chart. Flagging lets the dashboard highlight them while keeping the series
intact. The flag is information, not a filter.

**Q: How is `z_score` different from the `is_outlier` flag?**
A: `z_score` (in `enrich`) measures deviation from a short *trailing* 4-week
window using mean/std — it's a directional, real-time-friendly volatility signal
for the chart. `is_outlier` (in `clean`) is a robust, *centered* Hampel flag for
offline data-quality detection. Different windows, different statistics,
different purposes.

---

## Chunk 4 — Storage Layer (Repository Pattern)

### What was built
- `storage/repository.py` with `CommodityRepository`:
  - `__init__` opens a connection and calls `_initialize_schema()`.
  - `upsert_raw(df, series_id)` — idempotent `INSERT OR IGNORE`; returns count added.
  - `upsert_processed(df, series_id)` — true upsert via `ON CONFLICT DO UPDATE`.
  - `get_processed(series_id, start, end)` — returns an ordered DataFrame.
  - `get_latest_date(series_id)` — newest stored date (drives incremental fetch).
  - Context-manager support (`with CommodityRepository(...) as repo:`) + `close()`.
- Schema for `raw_prices` and `processed_prices` (+ a `is_outlier` column and
  helpful indexes), created idempotently with `CREATE TABLE IF NOT EXISTS`.

### Key ideas

**1. The Repository pattern = one place that knows SQL.**
Every other layer receives/returns plain DataFrames and never sees a query. The
payoff: swapping SQLite for Postgres means rewriting *this one file* — the
pipeline and dashboard don't change. That's decoupling you can demo.

**2. Idempotency, two different ways — and why.**
`raw_prices` uses `INSERT OR IGNORE`: raw observations are immutable facts, so a
re-run is a silent no-op (proven: 74 inserted, then 0). `processed_prices` uses
`ON CONFLICT(series_id, date) DO UPDATE`: derived analytics *change* when
recomputed (revised prices, shifted rolling windows), so a re-run must
*overwrite*. Same goal (no duplicates), opposite conflict policy, for principled
reasons.

**3. The UNIQUE constraint is what makes upserts possible.**
`UNIQUE(series_id, date)` is the key both conflict strategies hinge on. Without
it, "insert or ignore/update on duplicate" has no notion of "duplicate."

**4. Transactions via the connection context manager.**
`with self._conn:` begins a transaction that commits on success and rolls back
on exception — so a mid-batch failure can't leave half-written data. Writes are
wrapped this way; the repo itself is also a context manager for connection
lifecycle.

**5. Thread-safety for the dashboard.**
Dash runs callbacks on worker threads, and a SQLite `Connection` isn't usable
across threads by default. I open with `check_same_thread=False` and serialize
writes with a `threading.Lock`. Verified with 8 concurrent writers: no errors,
correct final row count.

**6. The pandas ↔ SQLite impedance mismatch.**
`_to_records` handles three gaps: tag every row with `series_id`; normalize
`date` to an ISO string (which sorts chronologically as TEXT, so range queries
work); and convert pandas `NaN`/`NA` to Python `None` so warm-up rows become SQL
`NULL`, not the string `'nan'`. Verified the leading rolling-window rows store as
NULL.

**7. `get_latest_date` enables incremental loads.**
Returning the newest stored date lets the orchestrator (Chunk 5) fetch only
what's missing instead of re-downloading all history every run.

### Mistakes & fixes
- **`:memory:` vs connection-per-operation.** I considered opening a fresh
  connection per call (clean and thread-safe), but each new connection to
  `":memory:"` gets its *own empty database* — which would break the in-memory
  test DB the suite relies on. **Fix:** a single long-lived connection
  (`check_same_thread=False` + a write lock), which works for both file and
  in-memory databases and across Dash threads.
- **black wrapped a long SQL string / log line.** Ran `black`; adopted it.

### Interview Q&A

**Q: What is the Repository pattern and why use it here?**
A: It's an abstraction that isolates all data-access logic behind a collection-
like interface. Here, `CommodityRepository` is the only code that touches SQL;
everything else trades in DataFrames. That decouples business logic from storage
— I could move to Postgres/RDS by rewriting one file, and I can unit-test other
layers without a database.

**Q: How do you make writes idempotent, and why two strategies?**
A: A `UNIQUE(series_id, date)` constraint plus a conflict policy. Raw data uses
`INSERT OR IGNORE` because observations are immutable — re-running shouldn't
change anything. Processed data uses `ON CONFLICT DO UPDATE` because recomputed
analytics legitimately change and should overwrite. Both prevent duplicates;
they differ on what "already exists" should mean.

**Q: How do you handle transactions and partial failures?**
A: I wrap writes in `with self._conn:`, SQLite's transaction context manager. It
commits if the block succeeds and rolls back on any exception, so a batch either
lands fully or not at all — no half-written state.

**Q: Is your repository safe to use from the Dash dashboard's threads?**
A: Yes. SQLite connections default to single-thread use, so I set
`check_same_thread=False` and guard writes with a `threading.Lock`. I verified
with 8 concurrent writer threads — zero errors and the expected row count. For a
file DB I also enable WAL for better read/write concurrency.

**Q: How do pandas NaNs end up in the database?**
A: As SQL `NULL`. In `_to_records` I convert `NaN`/`NA` to Python `None` before
`executemany`, otherwise they could land as the literal string `'nan'`. The
leading rolling-window warm-up rows are the real-world case, and I confirmed they
store as NULL and read back as NaN.

**Q: Why store dates as TEXT instead of a date type?**
A: SQLite has no native date type — it stores dates as TEXT, REAL, or INTEGER. I
use ISO-8601 TEXT (`YYYY-MM-DD`), which sorts lexicographically identically to
chronologically, so `WHERE date >= ?` range filters work directly without
conversion.

---

## Chunk 5 — Orchestration Script

### What was built
- `run_pipeline.py` at the project root — the single command that wires Chunks
  2–4 into an end-to-end run: **fetch → upsert raw → recompute processed → upsert**.
- An `argparse` CLI: `--series` (one or more catalog keys, validated),
  `--start`, `--end`, `--refresh`.
- Incremental loading by default via `get_latest_date`; `--refresh` forces a
  full re-fetch.
- A per-series summary table (fetched / new raw / processed / status) plus totals
  and elapsed time. Returns a non-zero exit code only if *every* series failed.

### Key ideas

**1. Orchestration is composition, not logic.**
The script owns *no* business logic — it instantiates the client, pipeline, and
repository and sequences them. All the real work lives in the tested layers. The
orchestrator just decides *what runs in what order* and reports the outcome.

**2. Raw is the source of truth; processed is a derived view.**
The pivotal design choice: ingest raw incrementally (cheap, idempotent), then
read the **entire** raw history back from the DB and recompute the processed
table over all of it. This is a mini *lambda architecture*. Why? Rolling-window
analytics need contiguous history — recomputing only a small incremental batch
would produce wrong 4-week means and z-scores at the batch boundary. You can see
this in action: a run that fetched a 26-row window still wrote 104 processed rows
because the derived view is always rebuilt in full.

**3. Incremental fetch with a deliberate one-period overlap.**
By default the fetch starts at the latest stored date (not the day after), so an
upstream *revision* to the most recent point is caught. `INSERT OR IGNORE` makes
the overlap a harmless no-op. `--refresh` bypasses this and re-fetches the whole
window.

**4. Resilience: one bad series doesn't sink the run.**
A fetch failure is caught per series, recorded as `fetch-failed`, and the loop
continues. The exit code is non-zero only if *all* series failed — useful for
cron/CI to distinguish "partial" from "total" failure.

**5. A real CLI, validated for free.**
`argparse` with `choices=list(COMMODITIES)` rejects typos (`--series BOGUS`) with
a helpful message before any work runs — input validation at the boundary.

**6. Observability: a summary you can read at a glance.**
The run prints a table (fetched / new / processed / status) and total elapsed
time, so the operator immediately sees what changed without digging through logs.

### Mistakes & fixes
- **The incremental-vs-correctness trap.** My first instinct was to enrich only
  the freshly fetched rows and store them — but incremental batches don't have
  enough trailing history, so the rolling stats at the batch edge would be wrong.
  **Fix:** separate ingestion from derivation — ingest raw incrementally, then
  recompute processed from the *full* raw history (added `get_raw` to the
  repository to support this).
- **black reflowed the summary f-strings.** Ran `black`; adopted it.

### Interview Q&A

**Q: How does your pipeline avoid re-downloading all the data every run?**
A: The repository exposes `get_latest_date(series_id)`. On a normal run I start
the fetch at that date (with a one-period overlap to catch revisions), so I only
pull what's new. `INSERT OR IGNORE` makes re-seeing existing rows a no-op.
`--refresh` overrides this to re-fetch the whole window.

**Q: If you fetch incrementally, how do the rolling-window stats stay correct?**
A: I don't compute analytics on the incremental batch. I ingest raw
incrementally, then read the *entire* raw series back and recompute the processed
table over all of it. Raw is the immutable source of truth; processed is a
derived view I can always rebuild. It's a small lambda-architecture split.

**Q: What happens if one commodity's API call fails mid-run?**
A: It's caught per series, marked `fetch-failed`, and the run continues with the
others. The process exits non-zero only if every series failed, so an automated
scheduler can tell a partial failure from a total one.

**Q: Why argparse instead of reading `sys.argv` yourself?**
A: It gives me typed flags, defaults, `--help`, and validation (`choices=`) for
free, and rejects bad input before any side effects. It's the standard library's
job — no reason to hand-roll it.

**Q: Is the script idempotent? What if I run it twice?**
A: Yes. The second run fetches the small overlap, inserts 0 new raw rows, and
(unless `--refresh`) skips the processed recompute — reporting `up-to-date`. No
duplicates, no wasted work. I verified this end-to-end.

---

## Chunk 6 — Forecasting Module

### What was built
- `analytics/forecasting.py` with:
  - `PriceForecaster` — a statsmodels **ARIMA(1,1,1)** wrapper:
    `fit(df)` → `predict(steps=8)` (date, forecast, lower, upper) → `summary()`.
  - `LinearTrendForecaster` — a scikit-learn OLS trend baseline with the same
    output contract, for comparison.
  - `NotFittedError` for predict/summary-before-fit.

### Key ideas

**1. Why ARIMA(1,1,1) — and why deliberately simple.**
ARIMA = AutoRegressive (p) Integrated (d) Moving-Average (q).
  * **AR(1)**: tomorrow depends on today (one lag).
  * **I(1)**: one order of *differencing*. Commodity prices are *non-stationary*
    (they trend and wander); modeling the week-to-week *change* instead of the
    raw level makes the series stationary, which ARIMA requires. The middle `1`
    is the whole reason this works on prices.
  * **MA(1)**: one lag of the forecast error.
A small model is *more defensible* than an overfit one — it won't memorize noise,
and I can explain every term. The goal is to demonstrate time-series literacy,
not to win a forecasting competition.

**2. Confidence intervals that widen with the horizon.**
`predict` returns 95% CI bounds (`alpha=0.05`), and the interval *grows* the
further out you forecast — because uncertainty compounds with each step. I
verified width is non-decreasing across the horizon (≈±4.6 at step 1, ≈±11.6 at
step 8). A forecast without an interval is a guess pretending to be a fact.

**3. A fluent, predictable interface.**
`fit` returns `self`, so `PriceForecaster().fit(df).predict()` chains. Both
forecasters share the exact same output columns (`date, forecast, lower, upper`),
so the dashboard can swap models without changing any plotting code — the
**Strategy pattern** in miniature.

**4. Fail loudly on misuse.**
`predict()`/`summary()` before `fit()` raise `NotFittedError` (mirroring
scikit-learn's own convention), and too-short input raises `ValueError` with the
exact requirement. Graceful, explicit failure beats a cryptic crash deep in
statsmodels.

**5. AIC/BIC for model quality.**
`summary()` exposes AIC and BIC — penalized likelihood scores for comparing
models (lower is better; both punish extra parameters). They're the standard way
to justify "why this order and not a bigger one."

**6. Rebuild forecast dates myself.**
I fit on a bare NumPy array (sidestepping statsmodels' date-frequency warnings)
and reconstruct future dates from the inferred cadence (`gaps.median()`). Robust
to the occasional missing week.

### Mistakes & fixes
- **`.round(3)` warned on the datetime column.** In the demo, rounding the whole
  forecast frame triggered a pandas warning (you can't round a datetime).
  **Fix:** round only the numeric columns and format the date separately.
- **statsmodels convergence/frequency chatter.** Fitting short or noisy series
  emits non-actionable warnings. **Fix:** fit on a NumPy array and suppress
  warnings within the `fit` call only.

### Interview Q&A

**Q: Why ARIMA(1,1,1) specifically?**
A: It's the simplest model that respects the data. Prices are non-stationary, so
I need at least one order of differencing — that's the middle `1`. One AR and one
MA term capture short-run momentum and shock correction. A small, interpretable
model is more defensible than a complex one that overfits noise; I can justify
every term and back it up with AIC/BIC.

**Q: What does the "I" (the middle 1) do, and why does it matter here?**
A: It differences the series — models the change from period to period instead of
the level. ARIMA assumes stationarity (stable mean/variance), and price levels
aren't stationary; their *changes* are much closer. Without differencing the
model would be misspecified.

**Q: Why do your confidence intervals get wider further out?**
A: Forecast uncertainty compounds. Each step is built on the previous (already
uncertain) prediction, so error accumulates and the interval widens. I verified
the width is monotonically non-decreasing across the horizon. It honestly
communicates that long-range forecasts are less certain.

**Q: How do you keep the dashboard independent of which model you use?**
A: Both forecasters expose the same `fit`/`predict` interface and return the same
columns (`date, forecast, lower, upper`). That's the Strategy pattern — the
dashboard depends on the contract, not the concrete model, so I can swap ARIMA
for the linear trend (or anything else) without touching the plotting code.

**Q: What are AIC and BIC?**
A: Information criteria for model comparison — they reward goodness of fit but
penalize extra parameters (BIC penalizes more). Lower is better. They're how I'd
justify the chosen ARIMA order over a more complex one without just eyeballing.

**Q: How does the model behave on very little data?**
A: `fit` requires at least 10 observations and raises a clear `ValueError`
otherwise, rather than producing a meaningless fit or crashing inside
statsmodels. Predict/summary before fit raise `NotFittedError`.

---

## Chunk 7 — Plotly Dash Dashboard

### What was built
- `dashboard/layout.py` — `build_layout()`: header, commodity dropdown,
  date-range picker, three KPI cards, and two charts (dark theme, inline tokens).
- `dashboard/callbacks.py` — pure builders (`compute_kpis`, `build_price_figure`,
  `build_zscore_figure`) plus a testable `render()` and `register_callbacks()`.
- `dashboard/app.py` — `create_app()` wiring layout + callbacks over the
  repository; module-level `app`/`server` for `python -m dashboard.app` and WSGI.

### Key ideas

**1. Logic separated from the framework (testable UI).**
The data→figure work lives in plain functions; `render()` returns the five
outputs given a repo and selection, and the Dash callback is a one-line wrapper
around it. So I unit-tested the entire interactive behavior — KPIs, traces, date
filtering, empty selections — *without a browser*, then confirmed the real HTTP
callback on top.

**2. The reactive callback model.**
Dash is declarative: a callback names its `Output`s and `Input`s, and the
framework re-runs it whenever an input changes. One callback fans out to five
outputs (two figures + three KPI cards) from three inputs (commodity + two
dates). No manual event wiring or DOM manipulation.

**3. Reusing the Strategy-pattern forecaster.**
The chart calls the same `PriceForecaster().fit().predict()` from Chunk 6 and
overlays the result. Because the forecaster's contract is fixed, the plotting
code doesn't care which model produced the numbers.

**4. Drawing a confidence band.**
The 95% interval is one filled trace: x goes out along the upper bound and back
along the lower (`x = [*dates, *dates[::-1]]`, `fill="toself"`). The forecast is
anchored to the last actual point so the dashed line connects seamlessly.

**5. Graceful degradation.**
Forecasting is best-effort: if ARIMA can't fit, the page still renders the
history (the forecast is just omitted). Empty selections produce on-theme
"no data" figures and em-dash KPIs instead of crashing. The UI never white-screens.

**6. Deployment-ready by construction.**
Exposing `server = app.server` at module level means `gunicorn
dashboard.app:server` works with no changes — the WSGI entry point a real
deployment (Chunk 9 stretch / AWS) needs.

### Mistakes & fixes
- **`app.run_server` is gone in Dash 4.x.** My first instinct was the old
  `run_server`, but Dash 4 *removes* it and raises `ObsoleteAttributeException`.
  **Fix:** probed the installed API first, then used `app.run(...)`. Lesson:
  verify the framework version's surface instead of trusting memory.
- **Redundant `except (ValueError, Exception)`.** Tidied to `except Exception`
  for the best-effort forecast.

### Interview Q&A

**Q: How do you test a dashboard without clicking around in a browser?**
A: I keep the logic out of the framework. All the data-to-figure work is in pure
functions, and a `render()` function returns the callback's outputs for a given
selection. I unit-test that directly — asserting on trace names, KPI strings,
and that date filters narrow the data. Then I separately confirm the HTTP layer
by POSTing to Dash's `/_dash-update-component` endpoint, which exercises the real
callback end to end.

**Q: Explain Dash's callback model.**
A: It's reactive and declarative. Each callback declares its `Output`s and
`Input`s; when any input's value changes in the browser, Dash calls the function
server-side and patches the outputs back into the page. I use one callback with
five outputs driven by three inputs — selecting a commodity or changing the dates
refreshes both charts and all three KPIs at once.

**Q: How do you draw the forecast confidence interval?**
A: As a single filled polygon — trace the upper bound left-to-right, then the
lower bound right-to-left, and `fill="toself"`. I anchor it (and the forecast
line) to the last observed point so it connects to the history cleanly.

**Q: What happens if the forecast model fails, or there's no data in range?**
A: It degrades gracefully. The forecast is wrapped in try/except — if it fails,
the history still renders without an overlay. An empty selection returns a
styled "no data" figure and em-dash KPIs. The page never crashes on bad input.

**Q: How would you deploy this?**
A: It's already WSGI-ready: I expose `server = app.server`, so
`gunicorn dashboard.app:server` runs it behind a production server. From there
it's a container or an EC2/Elastic Beanstalk instance, with the SQLite file (or,
at scale, RDS Postgres via the same repository interface) behind it.

---
