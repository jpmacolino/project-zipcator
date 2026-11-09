"""Golden scenarios (SPEC §8.3) against the REAL artifact, verified independently.

Expected values are NOT snapshots of this code's output. For each scenario they come from
``zc_testkit.reference``: the *unsimplified* raw Census polygons, a ``geographiclib`` circle and a
different equal-area projection (EPSG:6933), sharing no code with ``zipcator.search``. On top of
that each scenario asserts well-known public facts (e.g. 33602 is downtown Tampa's ZIP).

Run with ``uv run pytest -m data``; documented in docs/QA_REPORT.md.
"""

from __future__ import annotations

import re

import geopandas as gpd
import pandas as pd
import pytest
from geographiclib.geodesic import Geodesic
from zc_testkit import reference

from zipcator import data
from zipcator.models import SearchResult
from zipcator.search import find_zips

pytestmark = pytest.mark.data

MAX_DRIFT_PP = 0.5  # SPEC §8.2 tolerance for overlap %; the measured worst case is ~0.13
SLIVER_PCT = 0.05  # a ZIP present in only one result must be a sliver below this

SCENARIOS = {
    "1 Tampa downtown 5 mi": (27.9506, -82.4572, 5.0),
    "2 Okeechobee rural 10 mi": (27.2439, -80.8298, 10.0),
    "3 Miami Beach 5 mi": (25.7907, -80.1300, 5.0),
    "4 Texarkana border 15 mi": (33.4418, -94.0377, 15.0),
    "5 Jordan MT tiny 0.5 mi": (47.3178, -106.9111, 0.5),
    "6 Orlando 100 mi": (28.5383, -81.3792, 100.0),
    "7a Honolulu 10 mi": (21.3069, -157.8583, 10.0),
    "7b San Juan 10 mi": (18.4655, -66.1057, 10.0),
    "8 Boston 3 mi": (42.3601, -71.0589, 3.0),
}


@pytest.fixture(scope="module")
def zctas() -> gpd.GeoDataFrame:
    return data.load_zctas()


@pytest.fixture(scope="module")
def raw() -> gpd.GeoDataFrame:
    if not reference.RAW_ZCTA_ZIP.exists():  # fail loudly: a skip would hide the golden tests
        pytest.fail(
            f"{reference.RAW_ZCTA_ZIP} not found: the golden scenarios need the raw Census "
            "download. Run: uv run python scripts/build_data.py",
            pytrace=False,
        )
    return reference.load_raw()


@pytest.fixture(scope="module")
def runs(zctas: gpd.GeoDataFrame, raw: gpd.GeoDataFrame):  # type: ignore[no-untyped-def]
    """name -> (SearchResult from the artifact, reference DataFrame from the raw shapes)."""
    out = {}
    for name, (lat, lon, radius) in SCENARIOS.items():
        out[name] = (
            find_zips(zctas, (lat, lon), radius),
            reference.overlaps(raw, lat, lon, radius),
        )
    return out


def compare(res: SearchResult, ref: pd.DataFrame) -> float:
    """Assert the two agree on the ZIP set (up to slivers) and return the worst overlap drift."""
    got = res.matches.set_index("zip")["overlap_pct"]
    want = ref.set_index("zip")["overlap_pct"]
    only_one = got.index.symmetric_difference(want.index)
    for z in only_one:
        assert max(got.get(z, 0.0), want.get(z, 0.0)) < SLIVER_PCT, f"{z} in only one result"
    both = got.index.intersection(want.index)
    return float((got[both] - want[both]).abs().max()) if len(both) else 0.0


# ======================================================================== every scenario
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_artifact_search_agrees_with_the_independent_reference(runs, name: str) -> None:  # type: ignore[no-untyped-def]
    res, ref = runs[name]
    assert len(ref) > 0
    assert compare(res, ref) <= MAX_DRIFT_PP, name


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_home_zip_agrees_with_point_in_polygon_on_the_raw_shapes(runs, name: str) -> None:  # type: ignore[no-untyped-def]
    res, ref = runs[name]
    ref_home = set(ref.loc[ref["contains_center"], "zip"])
    got_home = set(res.matches.loc[res.matches["contains_center"], "zip"])
    assert got_home == ref_home, f"{name}: artifact {got_home} vs raw polygons {ref_home}"


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_overlap_areas_agree_in_square_miles(runs, name: str) -> None:  # type: ignore[no-untyped-def]
    res, ref = runs[name]
    got = res.matches.set_index("zip")["area_in_radius_sq_mi"]
    want = ref.set_index("zip")["area_sq_mi"]
    both = got.index.intersection(want.index)
    big = [z for z in both if want[z] > 0.5]  # relative error is meaningless for slivers
    assert big
    for z in big:
        assert got[z] == pytest.approx(want[z], rel=0.01), f"{name} {z}"


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_zips_are_text_sorted_unique_and_in_range(runs, name: str) -> None:  # type: ignore[no-untyped-def]
    res, _ = runs[name]
    zips = res.matches["zip"]
    assert zips.is_unique and all(isinstance(z, str) and re.fullmatch(r"\d{5}", z) for z in zips)
    assert ((res.matches["overlap_pct"] > 0) & (res.matches["overlap_pct"] <= 100)).all()
    home = res.matches["contains_center"]
    assert list(home) == sorted(home, reverse=True)  # home ZIP(s) first (SPEC §5.3 step 7)


# ======================================================================= scenario facts
def test_1_dense_urban_tampa(runs) -> None:  # type: ignore[no-untyped-def]
    res, _ = runs["1 Tampa downtown 5 mi"]
    assert res.home_zip == "33602"  # downtown Tampa's ZIP
    assert set(res.matches["state"]) == {"FL"}
    assert {"33602", "33606", "33605", "33607"} <= set(res.matches["zip"])
    assert 8 <= res.count <= 16


def test_2_rural_large_zips_centroid_only_would_miss_the_home_zip(runs, zctas) -> None:  # type: ignore[no-untyped-def]
    """SPEC §9.3: a ZIP that a centroid-only search misses is found by overlap."""
    res, ref = runs["2 Okeechobee rural 10 mi"]
    lat, lon, radius = SCENARIOS["2 Okeechobee rural 10 mi"]
    assert res.home_zip == "34972"  # Okeechobee, FL
    centroid_only = {
        row.zip
        for row in zctas.itertuples()
        if Geodesic.WGS84.Inverse(lat, lon, row.intpt_lat, row.intpt_lon)["s12"] / reference.MILE_M
        <= radius
    }
    overlap = set(res.matches["zip"])
    assert overlap == set(ref["zip"])
    missed = overlap - centroid_only
    assert "34972" in missed, "the home ZIP's representative point is outside the circle"
    assert len(overlap) > len(centroid_only)
    home = res.matches.set_index("zip").loc["34972"]
    assert home.center_distance_mi > radius and bool(home.contains_center)


def test_3_coastal_ocean_does_not_inflate_overlap(runs) -> None:  # type: ignore[no-untyped-def]
    res, ref = runs["3 Miami Beach 5 mi"]
    lat, lon, radius = SCENARIOS["3 Miami Beach 5 mi"]
    circle = reference.circle_area_sq_mi(lat, lon, radius)
    land_in_circle = res.total_area_in_radius_sq_mi
    assert land_in_circle == pytest.approx(ref["area_sq_mi"].sum(), rel=0.01)
    assert land_in_circle < 0.75 * circle, "a large share of the circle is Atlantic Ocean"
    assert "33139" in set(res.matches["zip"])  # South Beach
    assert (res.matches["overlap_pct"] <= 100.0).all()


def test_4_state_border_results_span_states(runs) -> None:  # type: ignore[no-untyped-def]
    res, _ = runs["4 Texarkana border 15 mi"]
    assert {"AR", "TX"} <= set(res.matches["state"])
    assert {"71854", "75501"} <= set(res.matches["zip"])  # Texarkana AR and Texarkana TX


def test_5_tiny_radius_inside_a_large_zip_gives_one_result(runs, raw) -> None:  # type: ignore[no-untyped-def]
    res, ref = runs["5 Jordan MT tiny 0.5 mi"]
    lat, lon, radius = SCENARIOS["5 Jordan MT tiny 0.5 mi"]
    assert list(res.matches["zip"]) == list(ref["zip"]) == ["59337"]  # Jordan, MT
    circle = reference.geodesic_circle(lat, lon, radius)
    assert raw.loc[raw["zip"] == "59337", "geometry"].iloc[0].covers(circle)  # wholly inside
    row = res.matches.iloc[0]
    assert row.overlap_pct < 0.1 and row.contains_center and row.zip_area_sq_mi > 500


def test_6_big_radius_orlando_count_and_sanity(runs) -> None:  # type: ignore[no-untyped-def]
    res, ref = runs["6 Orlando 100 mi"]
    assert set(res.matches["zip"]) - set(ref["zip"]) == set()  # nothing the reference lacks
    assert 400 <= res.count <= 600
    lat, lon, radius = SCENARIOS["6 Orlando 100 mi"]
    assert res.total_area_in_radius_sq_mi < reference.circle_area_sq_mi(lat, lon, radius)
    assert {"FL"} == set(res.matches["state"])  # 100 mi of central Florida stays in Florida
    assert res.home_zip is not None


def test_7_non_conus_honolulu_and_san_juan(runs) -> None:  # type: ignore[no-untyped-def]
    hon, _ = runs["7a Honolulu 10 mi"]
    assert set(hon.matches["state"]) == {"HI"} and hon.home_zip == "96813"  # downtown Honolulu
    assert all(z[:3] in ("967", "968") for z in hon.matches["zip"])  # Hawaii: 96701-96898
    sj, _ = runs["7b San Juan 10 mi"]
    assert set(sj.matches["state"]) == {"PR"} and sj.home_zip == "00901"  # Old San Juan
    assert all(z[:3] in ("006", "007", "008", "009") for z in sj.matches["zip"])  # PR: 00601-00988
    assert sj.matches["city"].notna().all()  # territory cities come from the GeoNames PR file


def test_8_leading_zero_zips_boston(runs) -> None:  # type: ignore[no-untyped-def]
    res, _ = runs["8 Boston 3 mi"]
    zips = set(res.matches["zip"])
    assert {"02108", "02109", "02110", "02111"} <= zips  # Beacon Hill, North End, Fin. District
    assert all(z.startswith("0") for z in zips if z.startswith("02")) and "2108" not in zips
    assert set(res.matches["state"]) == {"MA"}
    assert str(res.matches["zip"].dtype) in ("object", "str", "string")


# ================================================================= pinned independent numbers
#: Overlap % per ZIP for scenario 1, computed once by ``reference.overlaps`` on the unsimplified
#: raw shapes (geographiclib circle, EPSG:6933). Hard-coded, so a regression in *either*
#: implementation, or a change in the source data, is noticed. They are not this code's output.
TAMPA_5MI_PINNED = {
    "33602": 100.0,
    "33603": 100.0,
    "33604": 58.43,
    "33605": 100.0,
    "33606": 100.0,
    "33607": 76.15,
    "33609": 96.07,
    "33610": 37.93,
    "33611": 38.33,
    "33614": 52.09,
    "33619": 42.02,
    "33629": 94.42,
}


def test_tampa_overlaps_match_the_pinned_independent_numbers(runs) -> None:  # type: ignore[no-untyped-def]
    res, ref = runs["1 Tampa downtown 5 mi"]
    ref_pct = ref.set_index("zip")["overlap_pct"]
    assert set(ref_pct.index) == set(TAMPA_5MI_PINNED)
    for z, pct in TAMPA_5MI_PINNED.items():
        assert ref_pct[z] == pytest.approx(pct, abs=0.05), f"reference drifted for {z}"
    got = res.matches.set_index("zip")["overlap_pct"]
    assert set(got.index) == set(TAMPA_5MI_PINNED)
    for z, pct in TAMPA_5MI_PINNED.items():
        assert got[z] == pytest.approx(pct, abs=MAX_DRIFT_PP), f"artifact differs for {z}"


# ============================================ SPEC §9.3: rural regression with TRUE centroids
OKEECHOBEE = SCENARIOS["2 Okeechobee rural 10 mi"][:2]


@pytest.mark.parametrize("radius", [3.0, 5.0, 8.0, 10.0])
def test_9_3_a_single_centre_point_search_misses_the_zip_the_address_is_in(  # type: ignore[no-untyped-def]
    zctas, radius: float
) -> None:
    """Overlap finds the home ZIP (34972) and its neighbour (34974) at every radius from 3 to
    10 miles. A search that tests one centre point per ZIP, whichever sense of 'centre', never
    finds the home ZIP in that range: the representative point finds nothing at all, and the true
    polygon centroid finds only the neighbour (and only from 10 miles)."""
    lat, lon = OKEECHOBEE
    overlap = set(find_zips(zctas, (lat, lon), radius).matches["zip"])
    assert {"34972", "34974"} <= overlap
    centre = reference.centre_point_matches(zctas, lat, lon, radius)
    assert "34972" not in centre["representative point"]
    assert "34972" not in centre["polygon centroid"]
    assert centre["representative point"] == set()
    assert centre["polygon centroid"] <= {"34974"}
    assert (centre["polygon centroid"] == {"34974"}) is (radius == 10.0)


def test_9_3_the_home_zip_only_appears_for_a_centre_point_search_at_twenty_miles(zctas) -> None:  # type: ignore[no-untyped-def]
    lat, lon = OKEECHOBEE
    centre = reference.centre_point_matches(zctas, lat, lon, 20.0)
    assert "34972" in centre["polygon centroid"]  # its centroid is ~17 mi away
    assert "34972" not in centre["representative point"]  # its representative point is ~20.2 mi
