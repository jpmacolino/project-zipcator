"""Performance budgets from SPEC §6 and §5.3 (markers ``slow`` + ``data``).

Run with ``uv run pytest -m slow``. Budgets are the spec's own numbers; the measured values (see
docs/QA_REPORT.md) are several times lower, so these only fail on a real regression.
"""

from __future__ import annotations

import subprocess
import sys
import time

import geopandas as gpd
import pytest
from shapely.geometry import box

from zipcator import data, geo
from zipcator.search import find_zips

pytestmark = [pytest.mark.slow, pytest.mark.data]

DENSE_METROS = {
    "New York": (40.7128, -74.0060),
    "Chicago": (41.8781, -87.6298),
    "Los Angeles": (34.0522, -118.2437),
    "Orlando": (28.5383, -81.3792),
}


@pytest.fixture(scope="module")
def zctas() -> gpd.GeoDataFrame:
    return data.load_zctas()


def test_cold_data_load_in_a_fresh_process_is_within_five_seconds() -> None:
    """SPEC §6: data load <= 5 s cold. A fresh interpreter includes importing geopandas."""
    code = (
        "import time; t=time.perf_counter(); from zipcator import data; data.load_zctas(); "
        "print(time.perf_counter()-t)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip()) <= 5.0


@pytest.mark.parametrize("metro", list(DENSE_METROS))
def test_100_mile_search_in_a_dense_metro_takes_under_one_second(zctas, metro: str) -> None:  # type: ignore[no-untyped-def]
    center = DENSE_METROS[metro]
    find_zips(zctas, center, 0.5)  # builds the spatial index once (lazy, shared by every search)
    start = time.perf_counter()
    res = find_zips(zctas, center, 100.0)
    elapsed = time.perf_counter() - start
    assert res.count > 400, f"{metro}: only {res.count} ZIPs at 100 miles"
    assert elapsed <= 1.0, f"{metro}: {elapsed:.2f} s for {res.count} ZIPs"


def test_the_first_search_including_the_spatial_index_build_is_also_fast(
    zctas: gpd.GeoDataFrame,
) -> None:
    fresh = zctas.copy()  # a copy has no cached sindex
    start = time.perf_counter()
    find_zips(fresh, DENSE_METROS["New York"], 100.0)
    assert time.perf_counter() - start <= 2.0  # one-off index build + the search itself


def test_spatial_prefilter_for_100_miles_is_under_fifty_milliseconds(
    zctas: gpd.GeoDataFrame,
) -> None:
    """SPEC §5.3 step 2: the candidate prefilter must be < 50 ms for 100 mi."""
    _ = zctas.sindex  # warm
    circle = geo.geodesic_circle(*DENSE_METROS["New York"], 100.0)
    window = box(*circle.bounds)
    zctas.sindex.query(window)
    start = time.perf_counter()
    for _ in range(10):
        hits = zctas.sindex.query(window)
    per_call = (time.perf_counter() - start) / 10
    assert len(hits) > 400
    assert per_call < 0.050, f"{per_call * 1000:.1f} ms"


def test_small_radius_searches_are_effectively_instant(zctas: gpd.GeoDataFrame) -> None:
    find_zips(zctas, DENSE_METROS["Chicago"], 0.5)
    start = time.perf_counter()
    for _ in range(5):
        find_zips(zctas, DENSE_METROS["Chicago"], 3.0)
    assert (time.perf_counter() - start) / 5 < 0.25
