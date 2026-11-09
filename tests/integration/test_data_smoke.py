"""Smoke test of the real built artifact (SPEC §4.2/§4.3, phase 2 exit criteria).

Runs only with ``pytest -m data`` after ``scripts/build_data.py``. Expected values are
well-known public facts (which state/city/county a famous ZIP belongs to), not whatever the
build happened to output.
"""

from __future__ import annotations

import re
import time

import geopandas as gpd
import pandas as pd
import pytest

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
    assert pd.notna(out.stdout)
