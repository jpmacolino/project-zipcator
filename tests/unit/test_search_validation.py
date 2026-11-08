"""Input validation for find_zips (QA phase 1 #M3, #L5)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import box
from zc_testkit.synth import TAMPA, make_gdf

from zipcator.search import find_zips


def _gdf() -> gpd.GeoDataFrame:
    return make_gdf(
        {"33601": box(-1000, -1000, 1000, 1000), "02108": box(3000, 3000, 6000, 6000)},
    )


def test_integer_zip_column_is_rejected_not_silently_unpadded() -> None:
    """Regression: int ZIPs became '2108' (lost leading zero) with no error."""
    gdf = _gdf()
    gdf["zip"] = [33601, 2108]
    with pytest.raises(ValueError, match="zip"):
        find_zips(gdf, TAMPA, 10)


@pytest.mark.parametrize("bad", ["2108", "021080", "0210A", "02 08", ""])
def test_malformed_zip_strings_are_rejected(bad: str) -> None:
    gdf = _gdf()
    gdf.loc[1, "zip"] = bad
    with pytest.raises(ValueError, match="zip"):
        find_zips(gdf, TAMPA, 10)


def test_duplicate_zips_are_rejected() -> None:
    gdf = _gdf()
    gdf.loc[1, "zip"] = "33601"
    with pytest.raises(ValueError, match="duplicate"):
        find_zips(gdf, TAMPA, 10)


def test_malformed_zip_outside_the_circle_does_not_slow_or_fail_the_search() -> None:
    """Validation runs on candidates only (keeps the per-search cost off the 33k rows)."""
    gdf = make_gdf({"33601": box(-1000, -1000, 1000, 1000), "99999": box(60_000, 0, 61_000, 1000)})
    gdf.loc[1, "zip"] = "bad"
    assert find_zips(gdf, TAMPA, 10).count == 1


def test_crs_none_is_assumed_wgs84() -> None:
    gdf = _gdf().set_crs(None, allow_override=True)
    assert gdf.crs is None
    res = find_zips(gdf, TAMPA, 10)
    assert set(res.matches["zip"]) == {"33601", "02108"}
