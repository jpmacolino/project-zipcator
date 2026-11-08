"""Geodesic circle, local projections and distances (docs/SPEC.md §5.3).

All distances come from the WGS84 geodesic (``pyproj.Geod``); all area math happens in
a local Lambert Azimuthal Equal-Area projection centred on the search point. Degrees
are never used as a distance or area unit.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence

import numpy as np
from pyproj import CRS, Geod
from shapely.geometry import Polygon

logger = logging.getLogger(__name__)

MILES_TO_METERS = 1609.344
SQ_M_PER_SQ_MI = MILES_TO_METERS**2
MIN_CIRCLE_POINTS = 128
DEFAULT_CIRCLE_POINTS = 360  # one vertex per degree of azimuth

GEOD = Geod(ellps="WGS84")


class AntimeridianError(ValueError):
    """The search circle crosses ±180° longitude or encloses a pole.

    Decision (docs/DECISIONS.md): such searches are rejected with a clear message rather
    than split into two polygons. The only affected US area is the far-western Aleutians.
    """


def _check_center(lat: float, lon: float) -> None:
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"Invalid coordinates: lat={lat}, lon={lon}")


def geodesic_circle(
    lat: float, lon: float, radius_mi: float, n_points: int = DEFAULT_CIRCLE_POINTS
) -> Polygon:
    """Return the geodesic circle of ``radius_mi`` miles around (lat, lon) as a lon/lat polygon.

    Vertices are produced with the WGS84 forward geodesic problem at ``n_points`` evenly
    spaced azimuths (≥ 128, SPEC §5.3).
    """
    _check_center(lat, lon)
    if not (math.isfinite(radius_mi) and radius_mi > 0):
        raise ValueError(f"radius must be a positive, finite number of miles (got {radius_mi})")
    if n_points < MIN_CIRCLE_POINTS:
        raise ValueError(f"n_points must be >= {MIN_CIRCLE_POINTS} (got {n_points})")

    radius_m = radius_mi * MILES_TO_METERS
    # Pole check: the circle encloses a pole if the pole is within the radius.
    for pole_lat in (90.0, -90.0):
        if GEOD.inv(lon, lat, 0.0, pole_lat)[2] <= radius_m:
            raise AntimeridianError(
                "Search circle encloses a pole; this is not supported. Use a smaller radius."
            )

    azimuths = np.linspace(0.0, 360.0, n_points, endpoint=False)
    lons, lats, _ = GEOD.fwd(
        np.full(n_points, lon), np.full(n_points, lat), azimuths, np.full(n_points, radius_m)
    )
    # fwd() normalises longitudes to [-180, 180]; a large jump from the center means the
    # circle wrapped across the antimeridian.
    dlon = (np.asarray(lons) - lon + 180.0) % 360.0 - 180.0
    unwrapped = lon + dlon
    if np.any(np.abs(unwrapped) > 180.0):
        raise AntimeridianError(
            "Search circle crosses the antimeridian (180° longitude); "
            "this is not supported. Use a smaller radius or a different center."
        )
    ring = list(zip(unwrapped.tolist(), np.asarray(lats).tolist(), strict=True))
    return Polygon(ring)


def laea_crs(lat: float, lon: float) -> CRS:
    """Lambert Azimuthal Equal-Area CRS centred on (lat, lon); used for all area math."""
    return CRS.from_proj4(f"+proj=laea +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")


def aeqd_crs(lat: float, lon: float) -> CRS:
    """Azimuthal-equidistant CRS centred on (lat, lon).

    Distances from the projection centre are true geodesic distances, so the shortest
    planar distance from the centre to a polygon equals the geodesic nearest distance.
    """
    return CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")


def geodesic_distance_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """WGS84 geodesic distance in statute miles."""
    _, _, dist_m = GEOD.inv(lon1, lat1, lon2, lat2)
    return float(dist_m) / MILES_TO_METERS


def geodesic_distances_mi(
    lat: float, lon: float, lats: Sequence[float] | np.ndarray, lons: Sequence[float] | np.ndarray
) -> np.ndarray:
    """Vectorised distances (miles) from one point to many."""
    lats_a = np.asarray(lats, dtype=float)
    lons_a = np.asarray(lons, dtype=float)
    _, _, dist_m = GEOD.inv(np.full_like(lons_a, lon), np.full_like(lats_a, lat), lons_a, lats_a)
    return np.asarray(dist_m, dtype=float) / MILES_TO_METERS
