"""Radius search: every ZCTA whose area overlaps a geodesic circle (docs/SPEC.md §5.3).

Pipeline: geodesic circle → bbox prefilter via the spatial index → project candidates and
circle to a LAEA CRS centred on the search point → exact polygon intersection → overlap %.
"""

from __future__ import annotations

import logging
import math

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point, box

from zipcator import geo
from zipcator.models import MATCH_COLUMNS, SearchResult

logger = logging.getLogger(__name__)

#: Overlaps of at most this many square metres are treated as floating-point slivers
#: (e.g. two shapes that merely touch) and dropped. 1 m² is ~10⁻⁹ of the smallest circle.
MIN_OVERLAP_AREA_M2 = 1.0

#: Slack when comparing a computed overlap % to the user's threshold, so a ZIP that is
#: 100 % inside the circle is not lost to 99.9999999999 % float error.
_PCT_TOLERANCE = 1e-9

#: Distances are rounded to this many miles (~1.6 mm) before sorting so float noise cannot
#: defeat the ZIP tie-break.
_SORT_DISTANCE_DECIMALS = 6

REQUIRED_COLUMNS = ("zip", "state", "intpt_lat", "intpt_lon", "geometry")
_OPTIONAL_TEXT = ("city", "county")


def _validate(gdf: gpd.GeoDataFrame, min_overlap_pct: float) -> gpd.GeoDataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in gdf.columns]
    if missing:
        raise ValueError(f"ZCTA data is missing required column(s): {', '.join(missing)}")
    if not (math.isfinite(min_overlap_pct) and 0 <= min_overlap_pct <= 100):
        raise ValueError(f"min_overlap_pct must be between 0 and 100 (got {min_overlap_pct})")
    if gdf.crs is None:  # documented assumption: unlabeled input is lon/lat WGS84
        gdf = gdf.set_crs(4326)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)
    return gdf


def _validate_zips(cand: gpd.GeoDataFrame) -> None:
    """ZIPs must be unique 5-digit *strings* (CLAUDE.md: never ints; leading zeros).

    Checked on the candidates only, so the cost does not scale with all ~33k ZCTAs.
    """
    zips = cand["zip"]
    if len(zips) and not pd.api.types.is_string_dtype(zips):
        raise ValueError(f"'zip' column must hold 5-digit strings, not {zips.dtype}")
    bad = zips[~zips.astype(str).str.fullmatch(r"\d{5}")]
    if len(bad):
        raise ValueError(
            f"invalid zip value(s) (need 5 digits): {sorted(set(bad.astype(str)))[:5]}"
        )
    if zips.duplicated().any():
        dupes = sorted(set(zips[zips.duplicated()]))[:5]
        raise ValueError(f"duplicate zip value(s) in ZCTA data: {dupes}")


def _sorted(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """SPEC §5.3 step 7: contains_center first, then nearest distance, then ZIP."""
    keyed = frame.assign(
        _home=~frame["contains_center"].astype(bool),
        _near=frame["nearest_distance_mi"].round(_SORT_DISTANCE_DECIMALS),
    )
    keyed = keyed.sort_values(["_home", "_near", "zip"], kind="stable")
    return keyed.drop(columns=["_home", "_near"]).reset_index(drop=True)


def find_zips(
    zctas: gpd.GeoDataFrame,
    center: tuple[float, float],
    radius_mi: float,
    min_overlap_pct: float = 0.0,
) -> SearchResult:
    """Find every ZIP (ZCTA) overlapping ``radius_mi`` miles around ``center`` (lat, lon).

    The ZIP containing the center is always returned, even below ``min_overlap_pct``.
    Raises :class:`zipcator.geo.AntimeridianError` if the circle crosses ±180° or a pole,
    and ``ValueError`` for invalid inputs.
    """
    zctas = _validate(zctas, min_overlap_pct)
    lat, lon = center
    circle = geo.geodesic_circle(lat, lon, radius_mi)  # validates center and radius

    # 2. Candidate prefilter: spatial index on the circle's bounding box.
    hits = np.sort(zctas.sindex.query(box(*circle.bounds)))
    cand = zctas.iloc[hits]
    logger.debug("prefilter: %d of %d ZCTAs are candidates", len(cand), len(zctas))
    _validate_zips(cand)

    # 3. Project candidates and circle to LAEA centred on the search point.
    laea = geo.laea_crs(lat, lon)
    circle_m = gpd.GeoSeries([circle], crs=4326).to_crs(laea).iloc[0]
    cand_m = cand.geometry.to_crs(laea)
    invalid = ~cand_m.is_valid
    if invalid.any():  # data build repairs geometries; stay defensive for hand-made input
        logger.warning("repairing %d invalid geometries before intersecting", int(invalid.sum()))
        cand_m = cand_m.copy()
        cand_m[invalid] = shapely.make_valid(cand_m[invalid].to_numpy())

    # 4. Exact overlap; drop zero-area contacts and slivers.
    overlap_m2 = cand_m.intersection(circle_m).area.to_numpy()
    keep = overlap_m2 > MIN_OVERLAP_AREA_M2
    cand = cand.iloc[np.flatnonzero(keep)]
    cand_m = cand_m.iloc[np.flatnonzero(keep)]
    overlap_m2 = overlap_m2[keep]
    zip_m2 = cand_m.area.to_numpy()

    # 5. Per-row metrics.
    pct = np.clip(overlap_m2 / zip_m2 * 100.0, 0.0, 100.0) if len(cand) else np.empty(0)
    contains = (
        shapely.covers(cand.geometry.to_numpy(), Point(lon, lat))
        if len(cand)
        else np.empty(0, dtype=bool)
    )
    if len(cand):
        aeqd = geo.aeqd_crs(lat, lon)
        nearest_m = cand.geometry.to_crs(aeqd).distance(Point(0.0, 0.0)).to_numpy()
        center_mi = geo.geodesic_distances_mi(lat, lon, cand["intpt_lat"], cand["intpt_lon"])
    else:
        nearest_m = np.empty(0)
        center_mi = np.empty(0)

    population = (
        pd.array(cand["population"], dtype="Int64")
        if "population" in cand
        else pd.array([pd.NA] * len(cand), dtype="Int64")
    )
    frame = gpd.GeoDataFrame(
        {
            "zip": cand["zip"].astype(str).to_numpy(),
            **{
                c: (cand[c].to_numpy() if c in cand else [None] * len(cand)) for c in _OPTIONAL_TEXT
            },
            "state": cand["state"].to_numpy(),
            "overlap_pct": pct,
            "area_in_radius_sq_mi": overlap_m2 / geo.SQ_M_PER_SQ_MI,
            "zip_area_sq_mi": zip_m2 / geo.SQ_M_PER_SQ_MI,
            "nearest_distance_mi": nearest_m / geo.MILES_TO_METERS,
            "center_distance_mi": center_mi,
            "contains_center": contains.astype(bool),
            "population": population,
            "est_population_in_radius": population.to_numpy(dtype="float64", na_value=np.nan)
            * pct
            / 100.0,
            "intpt_lat": cand["intpt_lat"].to_numpy(dtype=float),
            "intpt_lon": cand["intpt_lon"].to_numpy(dtype=float),
        },
        geometry=cand.geometry.to_numpy(),
        crs="EPSG:4326",
    )
    frame = frame[[*MATCH_COLUMNS, "geometry"]]

    # 6. Minimum-overlap filter; the ZIP containing the center is always kept.
    show = (frame["overlap_pct"] >= min_overlap_pct - _PCT_TOLERANCE) | frame["contains_center"]
    matches = _sorted(frame[show])
    excluded = _sorted(frame[~show])
    return SearchResult(
        center=(lat, lon),
        radius_mi=radius_mi,
        min_overlap_pct=min_overlap_pct,
        circle=circle,
        matches=matches,
        excluded=excluded,
    )
