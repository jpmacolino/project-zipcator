"""Environment-variable configuration and paths (docs/SPEC.md §6).

Config comes from environment variables only; nothing secret lives in the repo.
"""

from __future__ import annotations

import os
from pathlib import Path

from zipcator import __version__

ENV_DATA_DIR = "ZIPCATOR_DATA_DIR"
ENV_USER_AGENT = "ZIPCATOR_USER_AGENT"
ENV_DISABLE_NOMINATIM = "ZIPCATOR_DISABLE_NOMINATIM"
ENV_CENSUS_API_KEY = "CENSUS_API_KEY"

#: Public repository, used in the default Nominatim User-Agent (usage policy requires one
#: that identifies the application). Change here if the repo moves.
REPO_URL = "https://github.com/jpmacolino/project-zipcator"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def data_dir() -> Path:
    """Directory holding the built artifact (default: ``<repo>/data``)."""
    env = os.environ.get(ENV_DATA_DIR, "").strip()
    return Path(env) if env else Path(__file__).resolve().parents[2] / "data"


def zcta_path() -> Path:
    return data_dir() / "zcta.parquet"


def metadata_path() -> Path:
    return data_dir() / "metadata.json"


def raw_dir() -> Path:
    return data_dir() / "raw"


def user_agent() -> str:
    env = os.environ.get(ENV_USER_AGENT, "").strip()
    return env or f"zipcator/{__version__} (+{REPO_URL})"


def nominatim_disabled() -> bool:
    return os.environ.get(ENV_DISABLE_NOMINATIM, "").strip().lower() in _TRUTHY


def census_api_key() -> str | None:
    return os.environ.get(ENV_CENSUS_API_KEY, "").strip() or None
