"""Typed result containers shared by the library, CLI and UI (docs/SPEC.md §5.2, §5.3)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

#: Columns of ``SearchResult.matches`` (besides ``geometry``) in display order.
MATCH_COLUMNS: tuple[str, ...] = (
    "zip",
    "city",
    "county",
    "state",
    "overlap_pct",
    "area_in_radius_sq_mi",
    "zip_area_sq_mi",
    "nearest_distance_mi",
    "center_distance_mi",
    "contains_center",
    "population",
    "est_population_in_radius",
    "intpt_lat",
    "intpt_lon",
)


@dataclass(frozen=True)
class GeocodeCandidate:
    """One geocoder hit."""

    lat: float
    lon: float
    matched_address: str
    source: str


@dataclass(frozen=True)
class GeocodeResult:
    """Best geocoder hit plus every candidate (the best one is ``candidates[0]``)."""

    lat: float
    lon: float
    matched_address: str
    source: str
    candidates: list[GeocodeCandidate] = field(default_factory=list)


@dataclass(frozen=True)
class ZipMatch:
    """Row-view of one ZIP in a search result."""

    zip: str
    city: str | None
    county: str | None
    state: str | None
    overlap_pct: float
    area_in_radius_sq_mi: float
    zip_area_sq_mi: float
    nearest_distance_mi: float
    center_distance_mi: float
    contains_center: bool
    population: int | None
    est_population_in_radius: float | None


def _opt_str(v: object) -> str | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA else str(v)


@dataclass
class SearchResult:
    """Outcome of :func:`zipcator.search.find_zips`.

    ``matches`` are the ZIPs to show (sorted per SPEC §5.3 step 7); ``excluded`` are ZIPs
    that overlap the circle but fall below the minimum-overlap threshold (used for the
    optional grey outlines on the map). Both are GeoDataFrames in EPSG:4326.
    """

    center: tuple[float, float]
    radius_mi: float
    min_overlap_pct: float
    circle: Polygon
    matches: gpd.GeoDataFrame
    excluded: gpd.GeoDataFrame

    @property
    def count(self) -> int:
        return len(self.matches)

    @property
    def home_zip(self) -> str | None:
        """The ZIP containing the search center (``None`` if it is in water / unmapped)."""
        home = self.matches[self.matches["contains_center"]]
        return None if home.empty else str(home.iloc[0]["zip"])

    @property
    def total_area_in_radius_sq_mi(self) -> float:
        return float(self.matches["area_in_radius_sq_mi"].sum())

    @property
    def total_est_population(self) -> float | None:
        """Sum of per-ZIP estimates (uniform-density assumption); ``None`` without population."""
        est = self.matches["est_population_in_radius"].dropna()
        return None if est.empty else float(est.sum())

    @property
    def population_complete(self) -> bool:
        """True only if *every* match has population data, i.e. the total is not understated."""
        est = self.matches["est_population_in_radius"]
        return bool(len(est) and est.notna().all())

    def to_dataframe(self) -> pd.DataFrame:
        """``matches`` without geometry (table / CSV export)."""
        return pd.DataFrame(self.matches.drop(columns="geometry"))

    def zip_matches(self) -> list[ZipMatch]:
        out: list[ZipMatch] = []
        for r in self.matches.itertuples(index=False):
            pop = None if pd.isna(r.population) else int(r.population)
            est = None if pd.isna(r.est_population_in_radius) else float(r.est_population_in_radius)
            out.append(
                ZipMatch(
                    zip=str(r.zip),
                    city=_opt_str(r.city),
                    county=_opt_str(r.county),
                    state=_opt_str(r.state),
                    overlap_pct=float(r.overlap_pct),
                    area_in_radius_sq_mi=float(r.area_in_radius_sq_mi),
                    zip_area_sq_mi=float(r.zip_area_sq_mi),
                    nearest_distance_mi=float(r.nearest_distance_mi),
                    center_distance_mi=float(r.center_distance_mi),
                    contains_center=bool(r.contains_center),
                    population=pop,
                    est_population_in_radius=est,
                )
            )
        return out
