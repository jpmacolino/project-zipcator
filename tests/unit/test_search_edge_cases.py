"""Edge cases and documented behaviours of find_zips (QA phase 1 #L2, #L6)."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon, box
from zc_testkit.synth import MILE_M, TAMPA, make_gdf

from zipcator import geo
from zipcator.search import find_zips


def test_center_on_shared_boundary_flags_both_zips() -> None:
    """DECISIONS.md: a center exactly on a shared edge makes both ZIPs `contains_center`."""
    lat, lon = TAMPA
    west = box(lon - 0.05, lat - 0.05, lon, lat + 0.05)
    east = box(lon, lat - 0.05, lon + 0.05, lat + 0.05)  # shared edge is the meridian at lon
    gdf = make_gdf({"00001": box(0, 0, 1, 1), "00002": box(0, 0, 1, 1)})
    gdf = gdf.set_geometry(gpd.GeoSeries([west, east], index=gdf.index, crs="EPSG:4326"))
    res = find_zips(gdf, TAMPA, 5)
    assert sorted(res.matches["zip"]) == ["00001", "00002"]
    assert res.matches["contains_center"].all()


def test_center_in_a_hole_is_not_contained() -> None:
    donut = Polygon(
        box(-5_000, -5_000, 5_000, 5_000).exterior.coords,
        [box(-1_000, -1_000, 1_000, 1_000).exterior.coords],
    )
    res = find_zips(make_gdf({"33601": donut}), TAMPA, 10)
    row = res.matches.iloc[0]
    assert not row.contains_center
    assert res.home_zip is None
    assert row.nearest_distance_mi == pytest.approx(1_000 / MILE_M, rel=0.01)
    # overlap excludes the hole: ring area / ring area, ring fully inside the circle
    assert row.overlap_pct == pytest.approx(100.0, abs=0.01)
    assert row.zip_area_sq_mi == pytest.approx((10_000**2 - 2_000**2) / MILE_M**2, rel=0.002)


def test_non_default_and_duplicate_index_are_handled() -> None:
    gdf = make_gdf({"33601": box(-1000, -1000, 1000, 1000), "02108": box(3000, 3000, 6000, 6000)})
    gdf.index = pd.Index([7, 7])
    res = find_zips(gdf, TAMPA, 10)
    assert sorted(res.matches["zip"]) == ["02108", "33601"]
    assert list(res.matches.index) == [0, 1]


def test_null_geometry_rows_and_missing_text_fields_are_tolerated() -> None:
    gdf = make_gdf({"33601": box(-1000, -1000, 1000, 1000), "02108": box(3000, 3000, 6000, 6000)})
    gdf["city"] = [None, "Somewhere"]
    gdf["county"] = [np.nan, "X County"]
    extra = gdf.iloc[[0]].copy()
    extra["zip"] = "00000"
    extra = extra.set_geometry(gpd.GeoSeries([None], index=extra.index, crs="EPSG:4326"))
    gdf = pd.concat([gdf, extra], ignore_index=True)
    res = find_zips(gdf, TAMPA, 10)
    assert sorted(res.matches["zip"]) == ["02108", "33601"]
    by_zip = {m.zip: m for m in res.zip_matches()}
    assert by_zip["33601"].city is None and by_zip["33601"].county is None
    assert by_zip["02108"].city == "Somewhere"


@pytest.mark.parametrize(
    "population",
    [
        pd.Series([1000, None], dtype="object"),
        pd.Series([1000.0, np.nan], dtype="float64"),
        pd.Series([1000, pd.NA], dtype="Int64"),
    ],
)
def test_population_dtypes_all_work(population: pd.Series) -> None:
    gdf = make_gdf({"33601": box(-1000, -1000, 1000, 1000), "02108": box(3000, 3000, 6000, 6000)})
    gdf["population"] = population.to_numpy()
    res = find_zips(gdf, TAMPA, 10)
    rows = res.matches.set_index("zip")
    assert rows.loc["33601", "est_population_in_radius"] == pytest.approx(1000.0, abs=0.1)
    assert np.isnan(rows.loc["02108", "est_population_in_radius"])


def test_population_complete_flag_signals_partial_totals() -> None:
    """total_est_population sums only ZIPs with data; the flag tells callers it's partial."""
    full = make_gdf(
        {"33601": box(-1000, -1000, 1000, 1000), "02108": box(3000, 3000, 6000, 6000)},
        population={"33601": 10, "02108": 20},
    )
    assert find_zips(full, TAMPA, 10).population_complete is True
    partial = make_gdf(
        {"33601": box(-1000, -1000, 1000, 1000), "02108": box(3000, 3000, 6000, 6000)},
        population={"33601": 10},
    )
    res = find_zips(partial, TAMPA, 10)
    assert res.total_est_population == pytest.approx(10.0, abs=0.01)
    assert res.population_complete is False
    none = find_zips(make_gdf({"33601": box(-1000, -1000, 1000, 1000)}), TAMPA, 10)
    assert none.population_complete is False
    empty = find_zips(make_gdf({"99999": box(60_000, 0, 61_000, 1000)}), TAMPA, 10)
    assert empty.population_complete is False


@pytest.mark.parametrize("lon", [180.0, -180.0])
def test_center_exactly_on_antimeridian_is_rejected(lon: float) -> None:
    with pytest.raises(geo.AntimeridianError):
        geo.geodesic_circle(52.0, lon, 5.0)


def test_near_pole_but_not_enclosing_is_allowed() -> None:
    circle = geo.geodesic_circle(89.0, 0.0, 60.0)  # pole is ~69 mi away
    assert circle.is_valid and circle.area > 0
