"""Synthetic ZCTA builders for tests (docs/SPEC.md §8.2).

Shapes are authored in *metres in a local LAEA frame centred on the search point*, so
their dimensions relative to the circle are known exactly and expected overlaps can be
derived analytically. They are then segmentized (so straight edges stay straight after
the round trip) and converted to lon/lat, which is what the library consumes.

This module deliberately does NOT import ``zipcator`` so expectations stay independent
of the code under test.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import geopandas as gpd
import pandas as pd
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

MILE_M = 1609.344
TAMPA = (27.9506, -82.4572)  # (lat, lon)
SEGMENT_M = 250.0

CENTERS = {
    "tampa": TAMPA,
    "equator": (0.0, -80.0),
    "fairbanks": (64.8378, -147.7164),
    "honolulu": (21.3069, -157.8583),
    "san_juan": (18.4655, -66.1057),
    "boston": (42.3601, -71.0589),
}


def laea(lat: float, lon: float) -> CRS:
    return CRS.from_proj4(f"+proj=laea +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")


def to_lonlat(geom: BaseGeometry, center: tuple[float, float]) -> BaseGeometry:
    """Convert a metre-frame geometry (origin = ``center``) to a lon/lat geometry."""
    lat, lon = center
    t = Transformer.from_crs(laea(lat, lon), "EPSG:4326", always_xy=True)
    dense = shapely.segmentize(geom, SEGMENT_M)
    return shapely.transform(dense, lambda a: _apply(t, a))


def _apply(t: Transformer, coords: object) -> object:
    import numpy as np

    arr = np.asarray(coords)
    x, y = t.transform(arr[:, 0], arr[:, 1])
    return np.column_stack([x, y])


def circle_segment_area(radius: float, a: float) -> float:
    """Area of the part of a disc (radius r) lying at x >= a, 0 <= a <= r. Analytic."""
    return radius**2 * math.acos(a / radius) - a * math.sqrt(radius**2 - a**2)


def make_gdf(
    zones: Mapping[str, BaseGeometry],
    center: tuple[float, float] = TAMPA,
    *,
    state: str | Mapping[str, str] = "FL",
    population: Mapping[str, int] | None = None,
) -> gpd.GeoDataFrame:
    """Build a ZCTA GeoDataFrame (EPSG:4326) matching the data.py schema."""
    rows = []
    for zip_code, metre_geom in zones.items():
        geom = to_lonlat(metre_geom, center)
        rp = geom.representative_point()
        rows.append(
            {
                "zip": zip_code,
                "state": state[zip_code] if isinstance(state, Mapping) else state,
                "county": f"County {zip_code}",
                "city": f"City {zip_code}",
                "land_area_sq_mi": metre_geom.area / (MILE_M**2),
                "intpt_lat": rp.y,
                "intpt_lon": rp.x,
                "population": (population or {}).get(zip_code, pd.NA),
                "geometry": geom,
            }
        )
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf["population"] = gdf["population"].astype("Int64")
    return gdf


def multi(*polys: Polygon) -> MultiPolygon:
    return MultiPolygon(list(polys))


__all__ = [
    "CENTERS",
    "MILE_M",
    "TAMPA",
    "Point",
    "box",
    "circle_segment_area",
    "laea",
    "make_gdf",
    "multi",
    "to_lonlat",
]
