"""Smoke test of the real built artifact (SPEC §4.2/§4.3, phase 2 exit criteria).

Runs only with ``pytest -m data`` after ``scripts/build_data.py``. Expected values are
well-known public facts (which state/city/county a famous ZIP belongs to), not whatever the
build happened to output.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import geopandas as gpd
import pyogrio
import pytest
import shapely

from zipcator import config, data

pytestmark = pytest.mark.data


@pytest.fixture(scope="module")
def zctas() -> gpd.GeoDataFrame:
    return data.load_zctas()


def test_load_time_budget() -> None:
    start = time.perf_counter()
    data.load_zctas()
    assert time.perf_counter() - start < 5.0  # SPEC §6: cold load <= 5 s


def test_row_count_schema_and_crs(zctas: gpd.GeoDataFrame) -> None:
    assert len(zctas) >= 33_000
    assert list(zctas.columns) == [
        "zip",
        "state",
        "county",
        "city",
        "land_area_sq_mi",
        "intpt_lat",
        "intpt_lon",
        "population",
        "geometry",
    ]
    assert zctas.crs.to_epsg() == 4326


def test_zips_are_unique_five_digit_text_with_leading_zeros(zctas: gpd.GeoDataFrame) -> None:
    assert zctas["zip"].is_unique
    assert zctas["zip"].map(lambda z: bool(re.fullmatch(r"\d{5}", z))).all()
    assert zctas["zip"].str.startswith("0").sum() > 1000  # New England / NJ / PR are 0xxxx


def test_geometries_are_valid_and_nonempty(zctas: gpd.GeoDataFrame) -> None:
    assert zctas.geometry.notna().all()
    assert not zctas.geometry.is_empty.any()
    assert zctas.geometry.is_valid.all()
    assert set(zctas.geometry.geom_type) <= {"Polygon", "MultiPolygon"}


def test_every_row_has_state_county_and_city(zctas: gpd.GeoDataFrame) -> None:
    assert zctas["state"].str.fullmatch(r"[A-Z]{2}").all()
    assert zctas["county"].notna().all()
    assert zctas["city"].notna().all()  # territories filled from GeoNames PR/VI/GU/AS/MP


def test_state_coverage_is_50_states_dc_pr_and_territories(zctas: gpd.GeoDataFrame) -> None:
    states = set(zctas["state"])
    assert len(states) >= 52  # 50 states + DC + PR at minimum
    assert {"DC", "PR", "HI", "AK", "FL", "MA", "MT"} <= states


@pytest.mark.parametrize(
    ("zip_code", "state", "city", "county"),
    [
        ("02108", "MA", "Boston", "Suffolk County"),  # Beacon Hill; leading zero
        ("33602", "FL", "Tampa", "Hillsborough County"),
        ("90210", "CA", "Beverly Hills", "Los Angeles County"),
        ("10001", "NY", "New York", "New York County"),
        ("96815", "HI", "Honolulu", "Honolulu County"),
        ("99501", "AK", "Anchorage", "Anchorage Municipality"),
        ("00901", "PR", "San Juan", "San Juan Municipio"),
    ],
)
def test_famous_zips_have_the_known_attributes(
    zctas: gpd.GeoDataFrame, zip_code: str, state: str, city: str, county: str
) -> None:
    row = zctas.set_index("zip").loc[zip_code]
    assert (row.state, row.city, row.county) == (state, city, county)


def test_representative_points_lie_inside_their_polygons(zctas: gpd.GeoDataFrame) -> None:
    pts = gpd.GeoSeries(
        gpd.points_from_xy(zctas["intpt_lon"], zctas["intpt_lat"]), index=zctas.index, crs=4326
    )
    assert zctas.geometry.covers(pts).all()


def test_land_area_is_plausible(zctas: gpd.GeoDataFrame) -> None:
    assert (zctas["land_area_sq_mi"] >= 0).all()
    assert zctas["land_area_sq_mi"].max() < 20_000  # largest ZCTAs are Alaska/West, ~13,700
    # A famously tiny ZIP (Beacon Hill) is under a square mile; a huge Alaskan one is not.
    areas = zctas.set_index("zip")["land_area_sq_mi"]
    assert areas["02108"] < 1 < areas.max()


def test_population_absent_by_default(zctas: gpd.GeoDataFrame) -> None:
    assert str(zctas["population"].dtype) == "Int64"
    meta = data.load_metadata()
    assert meta is not None
    if not meta["population_included"]:
        assert zctas["population"].isna().all()


def test_metadata_is_complete_and_consistent(zctas: gpd.GeoDataFrame) -> None:
    meta = data.load_metadata()
    assert meta is not None
    assert meta["row_count"] == len(zctas)
    assert {s["name"] for s in meta["sources"]} >= {
        "zcta_boundaries",
        "county_relationship",
        "geonames",
    }
    assert all(s["url"].startswith("https://") and s["downloaded_at"] for s in meta["sources"])
    for key in ("built_at", "build_script_version", "simplification", "vintages"):
        assert key in meta
    assert "CC BY 4.0" in meta["vintages"]["geonames"]


def test_artifact_size_target() -> None:
    size_mb = config.zcta_path().stat().st_size / 1e6
    assert size_mb <= 50.0  # SPEC §4.2: artifact <= ~50 MB


def test_artifact_is_gitignored() -> None:
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        ["git", "check-ignore", "data/zcta.parquet"], cwd=root, capture_output=True, text=True
    )
    assert out.returncode == 0, "data/zcta.parquet must be ignored by git"


# ------------------------------------- QA phase 2: no loss, clean partition, bounded drift
def _raw_path(name: str) -> Path:
    path = config.raw_dir() / name
    if not path.exists():
        pytest.skip(f"raw download {name} is not cached in {config.raw_dir()}")
    return path


def test_no_zcta_was_dropped_versus_the_raw_boundary_file(zctas: gpd.GeoDataFrame) -> None:
    """Independent of the build code: compare against the raw shapefile's own ZIP list."""
    raw = _raw_path("cb_2020_us_zcta520_500k.zip")
    raw_zips = pyogrio.read_dataframe(
        f"zip://{raw.resolve().as_posix()}", columns=["ZCTA5CE20"], read_geometry=False
    )["ZCTA5CE20"]
    assert set(raw_zips) == set(zctas["zip"])
    assert len(zctas) == len(raw_zips)
    meta = data.load_metadata()
    assert meta is not None
    assert meta["input_zcta_count"] == meta["row_count"] == len(zctas)


@pytest.fixture(scope="module")
def projected(zctas: gpd.GeoDataFrame) -> gpd.GeoSeries:
    return zctas.set_index("zip").geometry.to_crs(5070)  # equal-area CONUS albers (km² checks)


def test_neighbouring_zips_do_not_overlap_after_simplification(projected: gpd.GeoSeries) -> None:
    """The raw Census file is a clean partition (0 km² overlap). Per-polygon simplification
    produced 9,198 km² of pairwise overlap; coverage simplification must keep it ~0."""
    arr = projected.to_numpy()
    left, right = projected.sindex.query(projected, predicate="intersects")
    keep = left < right
    overlap_m2 = shapely.area(shapely.intersection(arr[left[keep]], arr[right[keep]])).sum()
    assert overlap_m2 / 1e6 < 0.01  # km² across the whole country


def test_total_area_is_preserved_by_simplification(projected: gpd.GeoSeries) -> None:
    raw = _raw_path("cb_2020_us_zcta520_500k.zip")
    exact = pyogrio.read_dataframe(f"zip://{raw.resolve().as_posix()}").to_crs(5070)
    ratio = projected.area.sum() / exact.geometry.area.sum()
    assert ratio == pytest.approx(1.0, abs=0.001)  # measured 0.99996 for the per-polygon version


#: Scenarios deliberately different from scripts/simplification_study.py's (which chose the
#: tolerance), so this checks the claim rather than the fit: (name, (lat, lon), radius_mi)
DRIFT_SCENARIOS = [
    ("Texarkana border, 15 mi", (33.4418, -94.0377), 15.0),
    ("Nags Head / Outer Banks, 3 mi", (35.9574, -75.6241), 3.0),
    ("Anchorage, 10 mi", (61.2181, -149.9003), 10.0),
    ("Brooklyn, 3 mi", (40.6782, -73.9442), 3.0),
    ("Key West, 10 mi", (24.5551, -81.7800), 10.0),
    ("Rural Montana (Jordan), 20 mi", (47.3178, -106.9111), 20.0),
]


def test_overlap_pct_drift_vs_unsimplified_shapes_stays_within_spec(
    zctas: gpd.GeoDataFrame,
) -> None:
    """SPEC §4.2: simplification may move overlap % by at most ±1 percentage point.

    The unsimplified reference is rebuilt from the raw downloads with tolerance 0.
    """
    from zipcator import data_build as db
    from zipcator.search import find_zips

    raw = config.raw_dir()
    exact = db.assemble(
        db.read_boundaries(_raw_path("cb_2020_us_zcta520_500k.zip")),
        db.read_county_relationship(_raw_path("tab20_zcta520_county20_natl.txt")),
        db.read_geonames(_raw_path("US.zip")),
        tolerance=0.0,
    )
    assert raw.exists()
    worst = 0.0
    for name, center, radius in DRIFT_SCENARIOS:
        want = find_zips(exact, center, radius).matches.set_index("zip")["overlap_pct"]
        got = find_zips(zctas, center, radius).matches.set_index("zip")["overlap_pct"]
        both = want.index.intersection(got.index)
        drift = float((got[both] - want[both]).abs().max())
        worst = max(worst, drift)
        assert drift <= 1.0, f"{name}: drift {drift:.3f} pp"
        only_one = want.index.symmetric_difference(got.index)
        # a ZIP present in only one result must be a sliver (< 0.05 %) in the other
        assert all(max(want.get(z, 0.0), got.get(z, 0.0)) < 0.05 for z in only_one), name
    assert worst <= 0.5  # measured worst is ~0.13 pp on the study scenarios
