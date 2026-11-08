"""Sort keys of SPEC §5.3 step 7 are each independently exercised (QA phase 1 #M2)."""

from __future__ import annotations

from shapely.geometry import box
from zc_testkit.synth import MILE_M, TAMPA, make_gdf

from zipcator.search import find_zips


def test_nearest_edge_distance_not_representative_point_distance_decides_order() -> None:
    """A huge ZIP with a near edge but far representative point must sort before a small
    ZIP whose edge is farther. Sorting by center_distance_mi would invert the order, and the
    ZIP-name tie-break would put '00001' first, so neither mistake can pass."""
    gdf = make_gdf(
        {
            "00001": box(8_000, -500, 9_000, 500),  # small; nearest edge 8 km, rep point ~8.5 km
            "00002": box(5_000, -50_000, 100_000, 50_000),  # huge; nearest edge 5 km, rep ~50 km
        }
    )
    res = find_zips(gdf, TAMPA, 10)
    rows = res.matches.set_index("zip")
    assert rows.loc["00002", "center_distance_mi"] > rows.loc["00001", "center_distance_mi"]
    assert rows.loc["00002", "nearest_distance_mi"] < rows.loc["00001", "nearest_distance_mi"]
    assert list(res.matches["zip"]) == ["00002", "00001"]


def test_home_zip_sorts_first_even_if_others_have_equal_zero_distance_edges() -> None:
    gdf = make_gdf(
        {
            "00009": box(-500, -500, 500, 500),  # home
            "00001": box(500.0001, -500, 3_000, 500),  # adjacent, nearest ~0.3 mi
        }
    )
    res = find_zips(gdf, TAMPA, 10)
    assert list(res.matches["zip"]) == ["00009", "00001"]
    assert res.matches.iloc[1]["nearest_distance_mi"] < 1_000 / MILE_M
