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
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    frame = _frame()
    frame.loc[0, "geometry"] = bowtie
    assert _problems(frame, min_rows=1, check_geometry_validity=False) == []
    assert any("invalid" in p for p in _problems(frame, min_rows=1, check_geometry_validity=True))
