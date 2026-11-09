"""Independent reference for the golden scenarios (SPEC §8.3, §9.2).

Everything here is deliberately different from ``zipcator.search`` so that agreement means
something:

=======================  ==============================  =====================================
                         ``zipcator.search``             this reference
=======================  ==============================  =====================================
polygons                 simplified artifact             **unsimplified** raw Census shapefile
circle                   ``pyproj.Geod.fwd`` (360 pts)   ``geographiclib`` Direct (3,600 pts)
equal-area projection    LAEA centred on the point       EPSG:6933 (global cylindrical EA)
spatial filter           ``sindex`` on the circle bbox   ``sindex`` on the exact circle
overlap area             vectorised GeoPandas            shapely, one polygon at a time
=======================  ==============================  =====================================

It imports nothing from ``zipcator``.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from geographiclib.geodesic import Geodesic
from shapely.geometry import Point, Polygon

ROOT = Path(__file__).resolve().parents[2]
RAW_ZCTA_ZIP = ROOT / "data" / "raw" / "cb_2020_us_zcta520_500k.zip"
MILE_M = 1609.344
SQ_M_PER_SQ_MI = MILE_M**2
EQUAL_AREA = "EPSG:6933"  # WGS 84 / NSIDC EASE-Grid 2.0 Global: equal-area worldwide


def load_raw() -> gpd.GeoDataFrame:
    """The raw, unsimplified D1 polygons. Labelled WGS84: NAD83 differs by ~1 m, far below the
    tolerances being checked."""
    raw = pyogrio.read_dataframe(f"zip://{RAW_ZCTA_ZIP.resolve().as_posix()}")
    raw = raw.rename(columns={"ZCTA5CE20": "zip"})[["zip", "geometry"]]
    return raw.set_crs(4326, allow_override=True)


def geodesic_circle(lat: float, lon: float, radius_mi: float, n: int = 3600) -> Polygon:
    pts = []
    for i in range(n):
        d = Geodesic.WGS84.Direct(lat, lon, 360.0 * i / n, radius_mi * MILE_M)
        pts.append((d["lon2"], d["lat2"]))
    return Polygon(pts)


def overlaps(raw: gpd.GeoDataFrame, lat: float, lon: float, radius_mi: float) -> pd.DataFrame:
    """Per overlapping ZIP: ``overlap_pct``, ``area_sq_mi``, ``contains_center`` (raw shapes)."""
    circle = geodesic_circle(lat, lon, radius_mi)
    idx = raw.sindex.query(circle, predicate="intersects")
    cand = raw.iloc[sorted(idx)]
    to_equal_area = gpd.GeoSeries(cand.geometry, crs=4326).to_crs(EQUAL_AREA)
    circle_m = gpd.GeoSeries([circle], crs=4326).to_crs(EQUAL_AREA).iloc[0]
    center = Point(lon, lat)
    rows = []
    for zip_code, geom_m, geom_deg in zip(cand["zip"], to_equal_area, cand.geometry, strict=True):
        geom_m = shapely.make_valid(geom_m)
        inter = geom_m.intersection(shapely.make_valid(circle_m)).area
        if inter <= 1.0:  # one square metre: slivers and zero-area contact
            continue
        rows.append(
            {
                "zip": zip_code,
                "overlap_pct": min(100.0, inter / geom_m.area * 100.0),
                "area_sq_mi": inter / SQ_M_PER_SQ_MI,
                "contains_center": bool(geom_deg.covers(center)),
            }
        )
    return pd.DataFrame(rows).sort_values("zip").reset_index(drop=True)


def circle_area_sq_mi(lat: float, lon: float, radius_mi: float) -> float:
    circle_m = gpd.GeoSeries([geodesic_circle(lat, lon, radius_mi)], crs=4326).to_crs(EQUAL_AREA)
    return float(circle_m.area.iloc[0] / SQ_M_PER_SQ_MI)
