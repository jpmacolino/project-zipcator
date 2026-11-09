"""Tests for zipcator.data: load_zctas + artifact validation (SPEC §4.2, §8.2 data)."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from zipcator import data


def _frame(**overrides: object) -> gpd.GeoDataFrame:
    rows = {
        "zip": ["02108", "33602"],
        "state": ["MA", "FL"],
        "county": ["Suffolk County", "Hillsborough County"],
        "city": ["Boston", None],
        "land_area_sq_mi": [0.1, 2.5],
        "intpt_lat": [42.35, 27.95],
        "intpt_lon": [-71.06, -82.46],
        "population": pd.array([3500, None], dtype="Int64"),
    }
    rows.update(overrides)
    geoms = [box(-71.07, 42.34, -71.05, 42.36), box(-82.47, 27.94, -82.45, 27.96)]
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")


def test_load_roundtrip_preserves_schema(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    _frame().to_parquet(path)
    gdf = data.load_zctas(path)
    assert gdf["zip"].tolist() == ["02108", "33602"]  # leading zero survives
    assert str(gdf["population"].dtype) == "Int64"
    assert gdf.crs.to_epsg() == 4326
    assert gdf.geometry.name == "geometry"
    assert pd.isna(gdf.loc[1, "city"]) and pd.isna(gdf.loc[1, "population"])


def test_load_defaults_to_configured_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    _frame().to_parquet(tmp_path / "zcta.parquet")
    assert len(data.load_zctas()) == 2


def test_missing_file_raises_clear_error_with_build_instruction(tmp_path: Path) -> None:
    with pytest.raises(data.DataNotFoundError) as exc:
        data.load_zctas(tmp_path / "nope.parquet")
    msg = str(exc.value)
    assert "build_data.py" in msg and "nope.parquet" in msg
    assert isinstance(exc.value, data.ZctaDataError)


def test_unreadable_file_is_a_format_error(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    path.write_text("this is not parquet")
    with pytest.raises(data.DataFormatError, match="read"):
        data.load_zctas(path)


def test_missing_column_is_a_format_error(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    _frame().drop(columns=["state"]).to_parquet(path)
    with pytest.raises(data.DataFormatError, match="state"):
        data.load_zctas(path)


def test_integer_zip_column_in_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    _frame(zip=[2108, 33602]).to_parquet(path)
    with pytest.raises(data.DataFormatError, match="zip"):
        data.load_zctas(path)


def test_non_wgs84_artifact_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    _frame().to_crs(5070).to_parquet(path)
    with pytest.raises(data.DataFormatError, match="4326"):
        data.load_zctas(path)


def test_population_float_column_is_coerced_to_nullable_int(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    _frame(population=[3500.0, float("nan")]).to_parquet(path)
    gdf = data.load_zctas(path)
    assert str(gdf["population"].dtype) == "Int64"
    assert gdf.loc[0, "population"] == 3500


def test_metadata_roundtrip_and_absence(tmp_path: Path) -> None:
    assert data.load_metadata(tmp_path / "metadata.json") is None
    (tmp_path / "metadata.json").write_text(json.dumps({"row_count": 2}))
    assert data.load_metadata(tmp_path / "metadata.json") == {"row_count": 2}


def test_corrupt_metadata_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "metadata.json").write_text("{not json")
    assert data.load_metadata(tmp_path / "metadata.json") is None


# ------------------------------------------------------------------- validate_zcta_frame
def _problems(gdf: gpd.GeoDataFrame, **kw: object) -> list[str]:
    return data.validate_zcta_frame(gdf, **kw)  # type: ignore[arg-type]


def test_valid_frame_has_no_problems() -> None:
    assert _problems(_frame(), min_rows=2, check_geometry_validity=True) == []


def test_min_rows_enforced() -> None:
    (p,) = _problems(_frame(), min_rows=33_000)
    assert "33,000" in p


@pytest.mark.parametrize("bad", ["2108", "021080", "0210A", ""])
def test_bad_zip_values_reported(bad: str) -> None:
    frame = _frame(zip=[bad, "33602"])
    assert any("zip" in p and "5" in p for p in _problems(frame, min_rows=1))


def test_duplicate_zip_reported() -> None:
    assert any("duplicate" in p for p in _problems(_frame(zip=["02108", "02108"]), min_rows=1))


@pytest.mark.parametrize("bad", [None, "", "  "])
def test_missing_state_reported(bad: str | None) -> None:
    assert any("state" in p for p in _problems(_frame(state=[bad, "FL"]), min_rows=1))


def test_null_and_empty_geometry_reported() -> None:
    frame = _frame()
    frame.loc[0, "geometry"] = None
    assert any("null" in p or "empty" in p for p in _problems(frame, min_rows=1))
    frame = _frame()
    frame.loc[0, "geometry"] = Polygon()
    assert any("empty" in p for p in _problems(frame, min_rows=1))


def test_invalid_geometry_reported_only_when_checked() -> None:
    # self-intersecting, but its left lobe still covers the row's representative point
    bowtie = Polygon([(-71.08, 42.33), (-71.03, 42.38), (-71.03, 42.33), (-71.08, 42.38)])
    frame = _frame()
    frame.loc[0, "geometry"] = bowtie
    assert _problems(frame, min_rows=1, check_geometry_validity=False) == []
    assert any("invalid" in p for p in _problems(frame, min_rows=1, check_geometry_validity=True))


# ----------------------------------------- QA phase 2: loader validates what find_zips assumes
def test_nan_representative_point_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    _frame(intpt_lat=[float("nan"), 27.95]).to_parquet(path)
    with pytest.raises(data.DataFormatError, match="intpt"):
        data.load_zctas(path)


def test_swapped_lat_lon_representative_points_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    _frame(intpt_lat=[-71.06, -82.46], intpt_lon=[42.35, 27.95]).to_parquet(path)
    with pytest.raises(data.DataFormatError, match="intpt"):
        data.load_zctas(path)


def test_representative_point_outside_its_polygon_is_reported() -> None:
    problems = _problems(_frame(intpt_lat=[42.50, 27.95]), min_rows=1)
    assert any("intpt" in p and "inside" in p for p in problems)


def test_null_geometry_does_not_also_trigger_a_misleading_intpt_problem() -> None:
    frame = _frame()
    frame.loc[0, "geometry"] = None
    problems = _problems(frame, min_rows=1)
    assert any("null" in p for p in problems)
    assert not any("intpt" in p for p in problems)


@pytest.mark.parametrize("bad", ["XX", "ZZ", "fl"])
def test_unknown_state_codes_are_reported(bad: str) -> None:
    assert any("state" in p for p in _problems(_frame(state=[bad, "FL"]), min_rows=1))


def test_negative_population_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    _frame(population=pd.array([-666666666, None], dtype="Int64")).to_parquet(path)
    with pytest.raises(data.DataFormatError, match="population"):
        data.load_zctas(path)


def test_wrong_crs_error_is_short_not_a_projjson_dump(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    _frame().to_crs("OGC:CRS84").to_parquet(path)
    # CRS84 is lon/lat WGS84 but not EPSG:4326; either accepted or rejected, never a blob.
    try:
        data.load_zctas(path)
    except data.DataFormatError as exc:
        assert len(str(exc)) < 400 and "PROJJSON" not in str(exc) and '"type"' not in str(exc)


def test_wrong_projected_crs_error_names_the_crs_briefly(tmp_path: Path) -> None:
    path = tmp_path / "zcta.parquet"
    _frame().to_crs(5070).to_parquet(path)
    with pytest.raises(data.DataFormatError) as exc:
        data.load_zctas(path)
    assert "5070" in str(exc.value) and len(str(exc.value)) < 400


def test_known_state_set_matches_the_build_mapping() -> None:
    from zipcator import states

    assert set(states.STATE_FIPS.values()) == data.VALID_STATES
    assert {"FL", "PR", "DC", "GU"} <= data.VALID_STATES
