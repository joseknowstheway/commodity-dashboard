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
