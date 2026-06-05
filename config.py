"""Central configuration for the Commodity Price Analytics Dashboard.

Loads settings from a local `.env` file (via python-dotenv) with sensible
defaults, and exposes the EIA series catalog used throughout the app.

Import the singleton `settings` object rather than reading os.environ directly:

    from config import settings, COMMODITIES
    print(settings.db_path)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load variables from `.env` into the environment, if the file exists.
# Existing environment variables are NOT overridden (override=False), so
# real env vars (e.g. in CI) take precedence over the file.
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Commodity:
    """A single tradable commodity and how to fetch it from the EIA v2 API.

    The EIA v2 API addresses data by a *route* (e.g. "petroleum/pri/spt") plus
    a *series* facet (e.g. "RWTC"), rather than the legacy v1 dotted series ID
    (e.g. "PET.RWTC.W"). `series_id` here is the v2 facet code and doubles as
    the stable primary identifier stored in the database.
    """

    key: str  # internal key, e.g. "WTI_CRUDE"
    label: str  # human-readable name for the UI
    route: str  # EIA v2 data route, e.g. "petroleum/pri/spt"
    series_id: str  # EIA v2 series facet code, e.g. "RWTC" (also the DB key)
    unit: str  # display unit, e.g. "$/bbl"
    frequency: str = "weekly"  # EIA frequency for this series


# EIA v2 series catalog. Routes/facets verified against https://api.eia.gov/v2.
COMMODITIES: dict[str, Commodity] = {
    "WTI_CRUDE": Commodity(
        key="WTI_CRUDE",
        label="WTI Crude Oil",
        route="petroleum/pri/spt",
        series_id="RWTC",
        unit="$/bbl",
    ),
    "NATURAL_GAS": Commodity(
        key="NATURAL_GAS",
        label="Natural Gas (Henry Hub)",
        route="natural-gas/pri/fut",
        series_id="RNGWHHD",
        unit="$/MMBtu",
    ),
    "GASOLINE": Commodity(
        key="GASOLINE",
        label="Regular Gasoline (US avg)",
        route="petroleum/pri/gnd",
        series_id="EMM_EPMR_PTE_NUS_DPG",
        unit="$/gal",
    ),
}


def _get_bool(name: str, default: bool) -> bool:
    """Parse a truthy/falsy environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration resolved from the environment."""

    eia_api_key: str
    db_path: Path
    default_commodity: str
    lookback_days: int
    eia_base_url: str

    def validate(self) -> None:
        """Raise a clear error if required settings are missing/invalid.

        Call this from entry points (pipeline, dashboard) that actually hit
        the API, so config-only imports (e.g. unit tests) never fail.
        """
        if not self.eia_api_key or self.eia_api_key == "your_eia_api_key_here":
            raise ValueError(
                "EIA_API_KEY is not set. Copy .env.example to .env and add "
                "your free key from https://www.eia.gov/opendata/"
            )
        if self.default_commodity not in COMMODITIES:
            valid = ", ".join(COMMODITIES)
            raise ValueError(
                f"DEFAULT_COMMODITY '{self.default_commodity}' is invalid. "
                f"Choose one of: {valid}"
            )
        if self.lookback_days <= 0:
            raise ValueError("LOOKBACK_DAYS must be a positive integer.")


def _load_settings() -> Settings:
    """Build the Settings object from environment variables."""
    db_path = Path(os.getenv("DB_PATH", "data/commodity.db"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    try:
        lookback_days = int(os.getenv("LOOKBACK_DAYS", "730"))
    except ValueError as exc:
        raise ValueError("LOOKBACK_DAYS must be an integer.") from exc

    return Settings(
        eia_api_key=os.getenv("EIA_API_KEY", ""),
        db_path=db_path,
        default_commodity=os.getenv("DEFAULT_COMMODITY", "WTI_CRUDE"),
        lookback_days=lookback_days,
        eia_base_url=os.getenv("EIA_BASE_URL", "https://api.eia.gov/v2"),
    )


# Singleton settings instance imported across the project.
settings = _load_settings()
