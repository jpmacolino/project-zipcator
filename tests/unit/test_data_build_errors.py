"""Error paths and geometry-repair details of zipcator.data_build."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
import requests
import responses
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon, box
from zc_testkit import raw_data as rd

from zipcator import data_build as db


def test_scrub_masks_secret_and_ignores_missing_one() -> None:
    assert db._scrub("GET /x?key=SECRET&a=1", "SECRET") == "GET /x?key=***&a=1"
    assert db._scrub("nothing", None) == "nothing"


def test_source_record_falls_back_to_mtime_without_sidecar(tmp_path: Path) -> None:
    f = tmp_path / "f.bin"
    f.write_bytes(b"abc")
    rec = db._source_record("x", "https://example.test/f.bin", f)
    assert rec["bytes"] == 3 and rec["downloaded_at"].endswith("Z") and rec["name"] == "x"


def test_source_record_prefers_sidecar(tmp_path: Path) -> None:
    f = tmp_path / "f.bin"
    f.write_bytes(b"abc")
    (tmp_path / "f.bin.meta.json").write_text(
        json.dumps({"downloaded_at": "2020-01-02T03:04:05Z", "bytes": 99})
    )
    rec = db._source_record("x", "u", f)
    assert rec["downloaded_at"] == "2020-01-02T03:04:05Z" and rec["bytes"] == 99


def test_unreadable_shapefile_zip_is_a_build_error(tmp_path: Path) -> None:
    p = tmp_path / "zcta.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("garbage.txt", "not a shapefile")
    with pytest.raises(db.BuildError, match="boundary shapefile"):
        db.read_boundaries(p)


def test_undecodable_relationship_file_is_a_build_error(tmp_path: Path) -> None:
    p = tmp_path / "rel.txt"
    p.write_bytes(b"\xff\xfe\x00\x80 not utf-8 \xc3\x28")
    with pytest.raises(db.BuildError, match="could not read"):
        db.read_county_relationship(p)


def test_geonames_zip_without_us_txt_is_a_build_error(tmp_path: Path) -> None:
    p = tmp_path / "US.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("readme.txt", "no data here")
    with pytest.raises(db.BuildError, match=r"US.txt"):
        db.read_geonames(p)


@responses.activate
def test_census_network_error_never_leaks_the_key() -> None:
    responses.get(
        "https://example.test/acs/2023/acs5",
        body=requests.ConnectionError("failed: https://x/?key=SECRETKEY"),
    )
    with pytest.raises(db.BuildError) as exc:
        db.fetch_population(
            year=2023, api_key="SECRETKEY", acs_url_template="https://example.test/acs/{year}/acs5"
        )
    assert "SECRETKEY" not in str(exc.value) and "network" in str(exc.value)


# ------------------------------------------------------------------------------- _polygonal
def test_polygonal_passes_polygons_and_drops_empty_or_none() -> None:
    sq = box(0, 0, 1, 1)
    assert db._polygonal(sq) is sq
    assert db._polygonal(None) is None
    assert db._polygonal(Polygon()) is None


def test_polygonal_extracts_polygons_from_a_geometry_collection() -> None:
    a, b = box(0, 0, 1, 1), box(5, 5, 6, 6)
    one = db._polygonal(GeometryCollection([a, LineString([(0, 0), (9, 9)]), Point(3, 3)]))
    assert one is not None and one.equals(a)
    many = db._polygonal(GeometryCollection([a, b, MultiPolygon([box(8, 8, 9, 9)])]))
    assert many is not None and many.geom_type == "MultiPolygon" and len(many.geoms) == 3
    assert many.area == pytest.approx(3.0)


def test_polygonal_returns_none_when_no_area_survives() -> None:
    assert db._polygonal(GeometryCollection([LineString([(0, 0), (1, 1)]), Point(2, 2)])) is None


def test_assemble_drops_zctas_with_no_polygon_area(caplog: pytest.LogCaptureFixture) -> None:
    sliver = LineString([(0, 0), (1, 1)]).buffer(0)  # degenerate: empty polygon
    boundaries = gpd.GeoDataFrame(
        {"zip": ["11111", "22222"], "land_area_sq_mi": [1.0, 1.0]},
        geometry=[box(0, 0, 1, 1), sliver],
        crs="EPSG:4326",
    )
    rel = pd.DataFrame({"zip": ["11111", "22222"], "state": ["FL", "FL"], "county": ["A", "B"]})
    gdf = db.assemble(boundaries, rel, pd.DataFrame(columns=["zip", "city"]), simplification=None)
    assert gdf["zip"].tolist() == ["11111"]
    assert "no polygon area" in caplog.text


@responses.activate
def test_build_rejects_html_error_page_served_for_a_zip(tmp_path: Path) -> None:
    responses.get("https://example.test/zcta.zip", body="<html>Gone</html>")
    src = db.Sources(
        zcta_url="https://example.test/zcta.zip",
        county_rel_url="https://example.test/rel.txt",
        geonames_url="https://example.test/US.zip",
        acs_url_template="https://example.test/{year}",
    )
    responses.get(src.county_rel_url, body=rd.relationship_text().encode())
    responses.get(src.geonames_url, body=rd.geonames_zip_bytes())
    with pytest.raises(db.BuildError, match="not a valid ZIP"):
        db.build(tmp_path / "d", sources=src)
