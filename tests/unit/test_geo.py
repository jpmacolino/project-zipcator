"""Tests for zipcator.geo (SPEC §5.3 steps 1 & 3, §8.2 'geo')."""

from __future__ import annotations

import math

import geopandas as gpd
import pytest
from geographiclib.geodesic import Geodesic
from pyproj import Transformer
from shapely.geometry import Point, Polygon

from zipcator import geo

MILE_M = 1609.344


def _laea_area_m2(circle: Polygon, lat: float, lon: float) -> float:
    # Independent proj string (not geo.laea_crs) so the test does not trust the code under test.
    crs = f"+proj=laea +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs"
    return float(gpd.GeoSeries([circle], crs="EPSG:4326").to_crs(crs).area.iloc[0])


@pytest.mark.parametrize("lat", [0.0, 28.0, 45.0, 64.0])
@pytest.mark.parametrize("radius_mi", [0.5, 10.0, 100.0])
def test_circle_projected_area_matches_pi_r_squared(lat: float, radius_mi: float) -> None:
    lon = -100.0
    circle = geo.geodesic_circle(lat, lon, radius_mi)
    expected = math.pi * (radius_mi * MILE_M) ** 2
    assert _laea_area_m2(circle, lat, lon) == pytest.approx(expected, rel=0.005)


def test_circle_has_at_least_128_vertices_and_is_closed_and_valid() -> None:
    circle = geo.geodesic_circle(28.0, -82.0, 10.0)
    assert len(circle.exterior.coords) - 1 >= 128
    assert circle.is_valid
    assert circle.exterior.coords[0] == circle.exterior.coords[-1]


def test_circle_vertices_are_at_geodesic_radius_from_center() -> None:
    """Every vertex is 10 mi from the center per an independent geodesic solver."""
    lat, lon = 45.0, -93.0
    circle = geo.geodesic_circle(lat, lon, 10.0)
    for x, y in list(circle.exterior.coords)[::17]:
        d = Geodesic.WGS84.Inverse(lat, lon, y, x)["s12"] / MILE_M
        assert d == pytest.approx(10.0, abs=1e-4)


def test_circle_contains_its_center_and_is_symmetric_enough() -> None:
    circle = geo.geodesic_circle(28.0, -82.0, 25.0)
    assert circle.contains(Point(-82.0, 28.0))
    minx, miny, maxx, maxy = circle.bounds
    assert (minx + maxx) / 2 == pytest.approx(-82.0, abs=1e-3)
    assert (miny + maxy) / 2 == pytest.approx(28.0, abs=0.01)


def test_custom_number_of_points() -> None:
    circle = geo.geodesic_circle(28.0, -82.0, 10.0, n_points=200)
    assert len(circle.exterior.coords) - 1 == 200


def test_too_few_points_rejected() -> None:
    with pytest.raises(ValueError, match="n_points"):
        geo.geodesic_circle(28.0, -82.0, 10.0, n_points=64)


@pytest.mark.parametrize("radius", [0.0, -1.0, float("nan"), float("inf")])
def test_bad_radius_rejected(radius: float) -> None:
    with pytest.raises(ValueError, match="radius"):
        geo.geodesic_circle(28.0, -82.0, radius)


@pytest.mark.parametrize(("lat", "lon"), [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)])
def test_bad_center_rejected(lat: float, lon: float) -> None:
    with pytest.raises(ValueError, match="coordinates"):
        geo.geodesic_circle(lat, lon, 5.0)


# --- antimeridian / poles (documented choice: reject with a clear message) -------------
def test_circle_crossing_antimeridian_is_rejected() -> None:
    with pytest.raises(geo.AntimeridianError, match="180"):
        geo.geodesic_circle(52.0, 179.9, 50.0)  # Aleutians-ish
    with pytest.raises(geo.AntimeridianError):
        geo.geodesic_circle(52.0, -179.9, 50.0)


def test_circle_near_but_not_crossing_antimeridian_is_ok() -> None:
    circle = geo.geodesic_circle(52.0, 175.0, 10.0)
    assert circle.bounds[2] < 180.0


def test_circle_enclosing_a_pole_is_rejected() -> None:
    with pytest.raises(geo.AntimeridianError, match="pole"):
        geo.geodesic_circle(89.0, 0.0, 100.0)


def test_antimeridian_error_is_a_value_error() -> None:
    assert issubclass(geo.AntimeridianError, ValueError)


# --- distances -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("p1", "p2"),
    [
        ((40.6413, -73.7781), (33.9416, -118.4085)),  # JFK -> LAX
        ((27.9506, -82.4572), (25.7617, -80.1918)),  # Tampa -> Miami
        ((21.3069, -157.8583), (18.4655, -66.1057)),  # Honolulu -> San Juan
        ((64.8378, -147.7164), (42.3601, -71.0589)),  # Fairbanks -> Boston
        ((0.0, 0.0), (0.0, 1.0)),
    ],
)
def test_geodesic_distance_matches_independent_solver(
    p1: tuple[float, float], p2: tuple[float, float]
) -> None:
    ref_m = Geodesic.WGS84.Inverse(p1[0], p1[1], p2[0], p2[1])["s12"]
    assert geo.geodesic_distance_mi(*p1, *p2) == pytest.approx(ref_m / MILE_M, rel=0.005)


@pytest.mark.parametrize(
    ("p1", "p2", "published_km"),
    [
        # Published WGS84 degree lengths (e.g. NGA / Wikipedia "Geographic coordinate system").
        ((0.0, 0.0), (1.0, 0.0), 110.574),  # 1 deg latitude at the equator
        ((44.5, 0.0), (45.5, 0.0), 111.132),  # 1 deg latitude at 45 deg
        ((0.0, 0.0), (0.0, 1.0), 111.320),  # 1 deg longitude at the equator
        ((45.0, 0.0), (45.0, 1.0), 78.847),  # 1 deg longitude at 45 deg
    ],
)
def test_geodesic_distance_matches_published_degree_lengths(
    p1: tuple[float, float], p2: tuple[float, float], published_km: float
) -> None:
    assert geo.geodesic_distance_mi(*p1, *p2) * MILE_M / 1000 == pytest.approx(
        published_km, rel=0.005
    )


def test_distance_zero_and_symmetric() -> None:
    assert geo.geodesic_distance_mi(28.0, -82.0, 28.0, -82.0) == 0.0
    a = geo.geodesic_distance_mi(28.0, -82.0, 30.0, -80.0)
    b = geo.geodesic_distance_mi(30.0, -80.0, 28.0, -82.0)
    assert a == pytest.approx(b, rel=1e-12)


def test_vectorised_distances_match_scalar() -> None:
    lats = [25.0, 30.0, 35.0]
    lons = [-80.0, -85.0, -90.0]
    vec = geo.geodesic_distances_mi(28.0, -82.0, lats, lons)
    for d, la, lo in zip(vec, lats, lons, strict=True):
        assert d == pytest.approx(geo.geodesic_distance_mi(28.0, -82.0, la, lo), rel=1e-12)


# --- projections ---------------------------------------------------------------------
def test_laea_crs_is_equal_area_centered_on_point() -> None:
    crs = geo.laea_crs(28.0, -82.0)
    t = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x, y = t.transform(-82.0, 28.0)
    assert (x, y) == pytest.approx((0.0, 0.0), abs=1e-6)
    wkt = crs.to_wkt()
    assert "Lambert Azimuthal Equal Area" in wkt
    assert crs.coordinate_operation is not None
    params = {p.name: p.value for p in crs.coordinate_operation.params}
    assert params["Latitude of natural origin"] == pytest.approx(28.0)
    assert params["Longitude of natural origin"] == pytest.approx(-82.0)


def test_aeqd_preserves_distance_from_center() -> None:
    lat, lon = 28.0, -82.0
    crs = geo.aeqd_crs(lat, lon)
    t = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x, y = t.transform(-80.0, 30.0)
    ref = Geodesic.WGS84.Inverse(lat, lon, 30.0, -80.0)["s12"]
    assert math.hypot(x, y) == pytest.approx(ref, rel=1e-6)
