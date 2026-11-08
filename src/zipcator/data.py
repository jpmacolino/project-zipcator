"""Loading and validating the processed ZCTA artifact (docs/SPEC.md §4.2).

``load_zctas`` is the only way the app and CLI read ``data/zcta.parquet``. It guarantees the
schema ``zipcator.search`` relies on: EPSG:4326, a ``geometry`` column, 5-digit *string* ZIPs,
nullable-``Int64`` population.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from zipcator import config

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS: tuple[str, ...] = (
    "zip",
    "state",
    "county",
    "city",
    "land_area_sq_mi",
    "intpt_lat",
    "intpt_lon",
    "population",
    "geometry",
)
#: Spec §4.3: the real build must yield at least this many ZCTAs.
MIN_ROWS = 33_000


class ZctaDataError(Exception):
    """Base class for problems with the ZCTA data artifact."""


class DataNotFoundError(ZctaDataError, FileNotFoundError):
    """The artifact has not been built yet."""


class DataFormatError(ZctaDataError, ValueError):
    """The artifact exists but is unreadable or does not have the expected schema."""


def validate_zcta_frame(
    gdf: gpd.GeoDataFrame, *, min_rows: int = 1, check_geometry_validity: bool = False
) -> list[str]:
    """Return a list of human-readable problems (empty list = valid). SPEC §4.3 checks."""
    missing = [c for c in REQUIRED_COLUMNS if c not in gdf.columns]
    if missing:
        return [f"missing column(s): {', '.join(missing)}"]
    problems: list[str] = []
    if len(gdf) < min_rows:
        problems.append(f"only {len(gdf):,} rows; expected at least {min_rows:,}")

    zips = gdf["zip"]
    if not pd.api.types.is_string_dtype(zips):
        problems.append(f"'zip' must be 5-digit strings, not {zips.dtype}")
    else:
        bad = zips[~zips.astype(str).str.fullmatch(r"\d{5}", na=False)]
        if len(bad):
            problems.append(f"{len(bad):,} zip value(s) are not 5 digits, e.g. {list(bad[:3])}")
    if zips.duplicated().any():
        problems.append(f"{int(zips.duplicated().sum()):,} duplicate zip value(s)")

    state = gdf["state"]
    no_state = state.isna() | (state.astype("string").str.strip() == "")
    if no_state.any():
        problems.append(
            f"{int(no_state.sum()):,} row(s) have no state, e.g. {list(zips[no_state][:3])}"
        )

    geom = gdf.geometry
    n_null = int(geom.isna().sum())
    if n_null:
        problems.append(f"{n_null:,} null geometries")
    n_empty = int(geom.is_empty.sum())  # is_empty is False for None; nulls are reported above
    if n_empty:
        problems.append(f"{n_empty:,} empty geometries")
    if check_geometry_validity:
        n_invalid = int((~geom[~geom.isna()].is_valid).sum())
        if n_invalid:
            problems.append(f"{n_invalid:,} invalid geometries")
    return problems


def load_zctas(path: Path | str | None = None) -> gpd.GeoDataFrame:
    """Load the built artifact (default ``<data dir>/zcta.parquet``).

    Raises :class:`DataNotFoundError` (with the build command) if it is missing and
    :class:`DataFormatError` if it cannot be read or fails the schema checks.
    """
    path = Path(path) if path is not None else config.zcta_path()
    if not path.exists():
        raise DataNotFoundError(
            f"ZIP boundary data not found at {path}. Build it once with: "
            "uv run python scripts/build_data.py"
        )
    try:
        gdf = gpd.read_parquet(path)
    except Exception as exc:
        raise DataFormatError(
            f"Could not read {path} ({exc}). Rebuild it with: "
            "uv run python scripts/build_data.py --refresh"
        ) from exc

    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        raise DataFormatError(f"{path} must be in EPSG:4326 (got {gdf.crs}); rebuild the data.")
    if "population" in gdf.columns:
        gdf["population"] = pd.to_numeric(gdf["population"], errors="coerce").astype("Int64")
    problems = validate_zcta_frame(gdf)
    if problems:
        raise DataFormatError(
            f"{path} failed validation: {'; '.join(problems)}. Rebuild it with: "
            "uv run python scripts/build_data.py --refresh"
        )
    return gdf


def load_metadata(path: Path | str | None = None) -> dict[str, Any] | None:
    """Return the build metadata dict, or ``None`` if absent or unreadable."""
    path = Path(path) if path is not None else config.metadata_path()
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None
