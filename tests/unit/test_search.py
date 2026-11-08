"""Tests for zipcator.search.find_zips (SPEC §5.3, §8.2 'search').

Synthetic ZCTAs are authored in metres in a local frame around the search center, so
expected overlaps are analytic (or computed with an independent planar/geodesic method),
never snapshotted from the code under test.
"""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pytest
from geographiclib.geodesic import Geodesic
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from shapely.geometry import Point, box
from zc_testkit.synth import (
    CENTERS,
    MILE_M,
    TAMPA,
    circle_segment_area,
    make_gdf,
    multi,
    to_lonlat,
)
from zipcator.models import SearchResult
from zipcator.search import find_zips

from zipcator import geo

R10 = 10 * MILE_M  # 10-mile radius in metres


def _row(res: SearchResult, zip_code: str):
    rows = res.matches[res.matches["zip"] == zip_code]
    assert len(rows) == 1, f"{zip_code} not in results: {list(res.matches['zip'])}"
    return rows.iloc[0]


# ----------------------------------------------------------------------------- basics
def test_polygon_fully_inside_circle_is_100_percent() -> None:
    gdf = make_gdf({"33601": box(3000, 3000, 6000, 6000)})
    res = find_zips(gdf, TAMPA, 10)
    assert _row(res, "33601").overlap_pct == pytest.approx(100.0, abs=0.01)
    assert res.count == 1


def test_disjoint_polygon_is_excluded() -> None:
    gdf = make_gdf(
        {"33601": box(3000, 3000, 6000, 6000), "99999": box(50_000, 50_000, 60_000, 60_000)}
    )
    res = find_zips(gdf, TAMPA, 10)
    assert list(res.matches["zip"]) == ["33601"]


def test_polygon_touching_nothing_far_away_gives_empty_result() -> None:
    gdf = make_gdf({"99999": box(50_000, 50_000, 60_000, 60_000)})
    res = find_zips(gdf, TAMPA, 10)
    assert res.count == 0
    assert res.matches.empty
    assert res.total_area_in_radius_sq_mi == 0.0
    assert res.total_est_population is None
    assert "overlap_pct" in res.matches.columns  # friendly empty frame keeps its schema


def test_empty_input_gdf() -> None:
    gdf = make_gdf({"33601": box(0, 0, 1, 1)}).iloc[0:0]
    res = find_zips(gdf, TAMPA, 10)
    assert res.count == 0


def test_circle_split_by_straight_edge_matches_analytic_segment() -> None:
    """A huge rectangle whose west edge is a chord of the circle at x = a."""
    a = 2000.0
    gdf = make_gdf({"33602": box(a, -50_000, 100_000, 50_000)})
    res = find_zips(gdf, TAMPA, 10)
    zip_area = (100_000 - a) * 100_000
    expected = circle_segment_area(R10, a) / zip_area * 100
    assert _row(res, "33602").overlap_pct == pytest.approx(expected, abs=0.5)
    assert _row(res, "33602").area_in_radius_sq_mi == pytest.approx(
        circle_segment_area(R10, a) / MILE_M**2, rel=0.005
    )


def test_two_zips_tiling_a_strip_sum_to_disc_minus_caps() -> None:
    """Two huge ZIPs share a seam at x=0 and span |y| <= 9 km: together they cover the disc
    except the two circular caps beyond |y| > 9 km (analytic)."""
    gdf = make_gdf(
        {"33603": box(1, -9_000, 100_000, 9_000), "33604": box(-100_000, -9_000, -1, 9_000)}
    )
    res = find_zips(gdf, TAMPA, 10)
    total = _row(res, "33603").area_in_radius_sq_mi + _row(res, "33604").area_in_radius_sq_mi
    caps = 2 * circle_segment_area(R10, 9_000)
    expected = (math.pi * R10**2 - caps) / MILE_M**2
    assert total == pytest.approx(expected, rel=0.005)


def test_small_polygon_overlap_matches_high_resolution_planar_circle() -> None:
    """Independent method: exact planar disc (100k vertices) ∩ polygon, in the LAEA frame."""
    zone = box(5_000, -3_000, 25_000, 9_000)
    gdf = make_gdf({"33605": zone})
    res = find_zips(gdf, TAMPA, 10)
    disc = Point(0, 0).buffer(R10, quad_segs=25_000)
    expected = disc.intersection(zone).area / zone.area * 100
    assert _row(res, "33605").overlap_pct == pytest.approx(expected, abs=0.5)


def test_overlap_matches_independent_geodesic_sampling() -> None:
    """Cross-check with a method that shares no code with the library.

    Sample a dense grid inside the ZIP (in lon/lat) and test each sample point against the
    circle using the independent geographiclib solver.
    """
    zone = box(4_000, -6_000, 24_000, 12_000)
    gdf = make_gdf({"33606": zone})
    lat0, lon0 = TAMPA
    geom = gdf.geometry.iloc[0]
    minx, miny, maxx, maxy = geom.bounds
    n = 70
    xs = np.linspace(minx, maxx, n + 2)[1:-1]
    ys = np.linspace(miny, maxy, n + 2)[1:-1]
    inside = total = 0
    for x in xs:
        for y in ys:
            if not geom.contains(Point(x, y)):
                continue
            total += 1
            d = Geodesic.WGS84.Inverse(lat0, lon0, float(y), float(x))["s12"]
            inside += d <= R10
    expected = inside / total * 100
    res = find_zips(gdf, TAMPA, 10)
    assert _row(res, "33606").overlap_pct == pytest.approx(expected, abs=1.0)


# ------------------------------------------------------ rural / overlap-vs-centroid regressions
def test_big_polygon_containing_center_with_far_rep_point_is_included() -> None:
    """The key rural regression: the home ZIP is huge, its representative point is outside
    the radius, a centroid-only search would miss it entirely."""
    gdf = make_gdf({"59001": box(-5_000, -5_000, 120_000, 120_000)})
    res = find_zips(gdf, TAMPA, 10)
    row = _row(res, "59001")
    assert row.contains_center
    assert row.center_distance_mi > 10, "test setup: rep point must be outside the circle"
    assert row.nearest_distance_mi == 0
    assert 0 < row.overlap_pct < 5
    assert res.home_zip == "59001"


def test_big_polygon_whose_edge_enters_circle_but_centroid_far_is_included() -> None:
    gdf = make_gdf({"59002": box(10_000, -100_000, 200_000, 100_000)})
    res = find_zips(gdf, TAMPA, 10)
    row = _row(res, "59002")
    assert not row.contains_center
    assert row.center_distance_mi > 10
    assert row.nearest_distance_mi == pytest.approx(10_000 / MILE_M, rel=0.01)
    assert row.overlap_pct > 0


# ------------------------------------------------------------------------- min overlap filter
def _three_zip_gdf() -> gpd.GeoDataFrame:
    return make_gdf(
        {
            "10001": box(-1000, -1000, 1000, 1000),  # home, 100% inside
            "10002": box(0, 14_000, 20_000, 34_000),  # sliver at the edge
            "10003": box(2000, -3000, 5000, 3000),  # fully inside
        }
    )


def test_min_overlap_filters_low_overlap_zips() -> None:
    gdf = _three_zip_gdf()
    everything = find_zips(gdf, TAMPA, 10)
    pct = {r.zip: r.overlap_pct for r in everything.matches.itertuples()}
    assert 0 < pct["10002"] < 5  # analytic estimate ~2.8%
    assert pct["10003"] == pytest.approx(100.0)
    res = find_zips(gdf, TAMPA, 10, min_overlap_pct=5)
    assert set(res.matches["zip"]) == {"10001", "10003"}
    assert list(res.excluded["zip"]) == ["10002"]


def test_home_zip_exempt_from_min_overlap() -> None:
    gdf = make_gdf({"59001": box(-5_000, -5_000, 120_000, 120_000)})
    res = find_zips(gdf, TAMPA, 10, min_overlap_pct=50)
    assert list(res.matches["zip"]) == ["59001"]
    assert bool(res.matches.iloc[0].contains_center)


def test_min_overlap_100_keeps_fully_covered_zips_despite_float_error() -> None:
    res = find_zips(_three_zip_gdf(), TAMPA, 10, min_overlap_pct=100)
    assert set(res.matches["zip"]) == {"10001", "10003"}
    assert list(res.excluded["zip"]) == ["10002"]


@pytest.mark.parametrize("bad", [-1.0, 100.5, float("nan")])
def test_min_overlap_out_of_range_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="min_overlap_pct"):
        find_zips(_three_zip_gdf(), TAMPA, 10, min_overlap_pct=bad)


# --------------------------------------------------------------------------- special shapes
def test_multipolygon_overlap_covers_all_parts() -> None:
    a, b = box(3000, 3000, 5000, 5000), box(-9000, -9000, -7000, -7000)
    far = box(60_000, 60_000, 61_000, 61_000)  # island far outside the circle
    gdf = make_gdf({"96701": multi(a, b, far)})
    res = find_zips(gdf, TAMPA, 10)
    expected = (a.area + b.area) / (a.area + b.area + far.area) * 100
    assert _row(res, "96701").overlap_pct == pytest.approx(expected, abs=0.1)


def test_center_in_water_has_no_home_zip_but_still_returns_overlaps() -> None:
    gdf = make_gdf({"33701": box(3000, -4000, 9000, 4000), "33702": box(-9000, -4000, -3000, 4000)})
    res = find_zips(gdf, TAMPA, 10)
    assert res.count == 2
    assert not res.matches["contains_center"].any()
    assert res.home_zip is None


def test_tiny_radius_inside_one_zip_gives_exactly_one_result() -> None:
    gdf = make_gdf(
        {
            "59001": box(-50_000, -50_000, 50_000, 50_000),
            "59002": box(50_001, -50_000, 150_000, 50_000),
        }
    )
    res = find_zips(gdf, TAMPA, 0.5)
    assert list(res.matches["zip"]) == ["59001"]
    expected = math.pi * (0.5 * MILE_M) ** 2 / (100_000**2) * 100
    assert _row(res, "59001").overlap_pct == pytest.approx(expected, rel=0.01)


def test_results_span_states() -> None:
    gdf = make_gdf(
        {"71854": box(-9000, -4000, -50, 4000), "75501": box(50, -4000, 9000, 4000)},
        state={"71854": "AR", "75501": "TX"},
    )
    res = find_zips(gdf, TAMPA, 10)
    assert set(res.matches["state"]) == {"AR", "TX"}


@pytest.mark.parametrize("name", ["fairbanks", "honolulu", "san_juan", "equator", "boston"])
def test_non_conus_and_other_centers_give_correct_overlap(name: str) -> None:
    """LAEA centered on the point keeps AK/HI/PR correct: same analytic answer anywhere."""
    center = CENTERS[name]
    a = 2000.0
    gdf = make_gdf({"00001": box(a, -50_000, 100_000, 50_000)}, center)
    res = find_zips(gdf, center, 10)
    expected = circle_segment_area(R10, a) / ((100_000 - a) * 100_000) * 100
    assert _row(res, "00001").overlap_pct == pytest.approx(expected, abs=0.5)


def test_leading_zero_zip_stays_a_string() -> None:
    gdf = make_gdf({"02108": box(-1000, -1000, 1000, 1000)}, CENTERS["boston"])
    res = find_zips(gdf, CENTERS["boston"], 3)
    assert res.matches["zip"].tolist() == ["02108"]
    assert all(isinstance(z, str) and len(z) == 5 for z in res.matches["zip"])


def test_antimeridian_search_is_rejected_with_clear_message() -> None:
    gdf = make_gdf({"99546": box(-1000, -1000, 1000, 1000)}, (52.0, 179.9))
    with pytest.raises(geo.AntimeridianError, match="antimeridian"):
        find_zips(gdf, (52.0, 179.9), 50)


# ---------------------------------------------------------------------------- output columns
def test_distances_flags_and_columns() -> None:
    gdf = make_gdf(
        {"33601": box(-1000, -1000, 1000, 1000), "33602": box(8_000, -2000, 14_000, 2000)}
    )
    res = find_zips(gdf, TAMPA, 10)
    home = _row(res, "33601")
    other = _row(res, "33602")
    assert home.contains_center and home.nearest_distance_mi == 0
    assert not other.contains_center
    assert other.nearest_distance_mi == pytest.approx(8_000 / MILE_M, rel=0.005)
    # center distance: independent solver on the representative point
    ref = Geodesic.WGS84.Inverse(TAMPA[0], TAMPA[1], other.intpt_lat, other.intpt_lon)["s12"]
    assert other.center_distance_mi == pytest.approx(ref / MILE_M, rel=0.005)
    assert other.zip_area_sq_mi == pytest.approx((6_000 * 4_000) / MILE_M**2, rel=0.005)
    for col in (
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
        "geometry",
    ):
        assert col in res.matches.columns, col


def test_population_estimate_and_totals() -> None:
    gdf = make_gdf(
        {"33601": box(-1000, -1000, 1000, 1000), "33602": box(0, 14_000, 20_000, 34_000)},
        population={"33601": 1000, "33602": 10_000},
    )
    res = find_zips(gdf, TAMPA, 10)
    a, b = _row(res, "33601"), _row(res, "33602")
    assert a.est_population_in_radius == pytest.approx(1000.0, abs=0.5)
    assert b.est_population_in_radius == pytest.approx(10_000 * b.overlap_pct / 100)
    assert res.total_est_population == pytest.approx(
        a.est_population_in_radius + b.est_population_in_radius
    )
    assert res.total_area_in_radius_sq_mi == pytest.approx(
        a.area_in_radius_sq_mi + b.area_in_radius_sq_mi
    )


def test_missing_population_gives_none_total_and_nan_estimate() -> None:
    res = find_zips(make_gdf({"33601": box(-1000, -1000, 1000, 1000)}), TAMPA, 10)
    assert res.total_est_population is None
    assert math.isnan(float(res.matches.iloc[0].est_population_in_radius))


def test_circle_returned_for_map_is_the_geodesic_polygon() -> None:
    res = find_zips(make_gdf({"33601": box(-1000, -1000, 1000, 1000)}), TAMPA, 10)
    assert res.circle.equals(geo.geodesic_circle(*TAMPA, 10))
    assert len(res.circle.exterior.coords) - 1 >= 128
    assert res.center == TAMPA
    assert res.radius_mi == 10


def test_result_geometries_are_wgs84_lonlat() -> None:
    res = find_zips(make_gdf({"33601": box(-1000, -1000, 1000, 1000)}), TAMPA, 10)
    assert res.matches.crs is not None
    assert res.matches.crs.to_epsg() == 4326
    minx, miny, _, _ = res.matches.total_bounds
    assert -83 < minx < -82 and 27 < miny < 28


def test_zip_matches_dataclass_view() -> None:
    res = find_zips(make_gdf({"33601": box(-1000, -1000, 1000, 1000)}), TAMPA, 10)
    (m,) = res.zip_matches()
    assert m.zip == "33601" and m.contains_center and m.overlap_pct == pytest.approx(100, abs=0.01)


# --------------------------------------------------------------------------------- sorting
def test_sort_order_home_then_nearest_then_zip() -> None:
    gdf = make_gdf(
        {
            "00005": box(9_000, -1000, 12_000, 1000),  # farther
            "00004": box(4_000, -1000, 6_000, 1000),  # nearer
            "00003": box(-6_000, -1000, -4_000, 1000),  # same nearest distance as 00004
            "00009": box(-500, -500, 500, 500),  # home
        }
    )
    res = find_zips(gdf, TAMPA, 10)
    assert list(res.matches["zip"]) == ["00009", "00003", "00004", "00005"]


def test_sort_is_stable_regardless_of_input_order() -> None:
    zones = {
        "00002": box(4_000, -1000, 6_000, 1000),
        "00001": box(-6_000, -1000, -4_000, 1000),
    }
    fwd = find_zips(make_gdf(zones), TAMPA, 10)
    rev = find_zips(make_gdf(dict(reversed(list(zones.items())))), TAMPA, 10)
    assert list(fwd.matches["zip"]) == list(rev.matches["zip"]) == ["00001", "00002"]


# --------------------------------------------------------------------------------- validation
@pytest.mark.parametrize("radius", [0, -5, float("nan"), float("inf")])
def test_bad_radius_rejected(radius: float) -> None:
    with pytest.raises(ValueError, match="radius"):
        find_zips(_three_zip_gdf(), TAMPA, radius)


def test_missing_required_columns_rejected() -> None:
    gdf = _three_zip_gdf().drop(columns=["state"])
    with pytest.raises(ValueError, match="state"):
        find_zips(gdf, TAMPA, 10)


def test_polygon_touching_circle_at_a_single_point_is_not_a_match() -> None:
    """Zero-area contact (shares exactly one vertex with the circle) must not be returned."""
    circle = geo.geodesic_circle(*TAMPA, 10)
    east_x, east_y = max(circle.exterior.coords, key=lambda c: c[0])
    touching = box(east_x, east_y - 0.01, east_x + 0.1, east_y + 0.01)  # lon/lat degrees
    assert touching.intersects(circle)  # it does touch...
    gdf = make_gdf({"33601": box(0, 0, 1, 1)})
    gdf = gdf.set_geometry(gpd.GeoSeries([touching], index=gdf.index, crs="EPSG:4326"))
    assert find_zips(gdf, TAMPA, 10).count == 0  # ...but overlaps by zero area


def test_overlap_below_epsilon_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented 1 m² epsilon is what removes floating-point slivers."""
    from zipcator import search

    assert search.MIN_OVERLAP_AREA_M2 == 1.0
    gdf = make_gdf({"33601": box(-1000, -1000, 1000, 1000)})  # 4e6 m² overlap
    assert find_zips(gdf, TAMPA, 10).count == 1
    monkeypatch.setattr(search, "MIN_OVERLAP_AREA_M2", 5e6)
    assert find_zips(gdf, TAMPA, 10).count == 0


# --------------------------------------------------------------------------- properties
def _random_gdf(rects: list[tuple[float, float, float, float]]) -> gpd.GeoDataFrame:
    zones = {}
    for i, (x, y, w, h) in enumerate(rects):
        zones[f"{i:05d}"] = box(x, y, x + w, y + h)
    return make_gdf(zones)


rect_strategy = st.tuples(
    st.floats(-60_000, 60_000),
    st.floats(-60_000, 60_000),
    st.floats(500, 40_000),
    st.floats(500, 40_000),
)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(
    rects=st.lists(rect_strategy, min_size=1, max_size=6),
    r1=st.floats(0.5, 40, allow_nan=False),
    dr=st.floats(0.5, 40, allow_nan=False),
)
def test_properties_bounds_monotonicity_uniqueness(
    rects: list[tuple[float, float, float, float]], r1: float, dr: float
) -> None:
    gdf = _random_gdf(rects)
    r2 = r1 + dr
    small = find_zips(gdf, TAMPA, r1)
    big = find_zips(gdf, TAMPA, r2)
    for res in (small, big):
        pct = res.matches["overlap_pct"]
        assert ((pct >= 0) & (pct <= 100 + 1e-9)).all()
        assert res.matches["zip"].is_unique
        assert all(isinstance(z, str) and len(z) == 5 for z in res.matches["zip"])
    assert set(small.matches["zip"]) <= set(big.matches["zip"])
    big_pct = dict(zip(big.matches["zip"], big.matches["overlap_pct"], strict=True))
    for z, p in zip(small.matches["zip"], small.matches["overlap_pct"], strict=True):
        assert big_pct[z] >= p - 1e-9


def test_to_lonlat_helper_roundtrip_sanity() -> None:
    """Guard the test kit itself: a 1 km square really is ~1 km wide per an independent solver."""
    g = to_lonlat(box(0, 0, 1000, 1000), TAMPA)
    minx, miny, maxx, _ = g.bounds
    width = Geodesic.WGS84.Inverse(miny, minx, miny, maxx)["s12"]
    assert width == pytest.approx(1000, rel=0.01)
