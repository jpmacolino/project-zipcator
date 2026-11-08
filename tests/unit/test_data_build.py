"""Tests for zipcator.data_build on tiny synthetic inputs (SPEC §4, §8.2 data_build).

HTTP is mocked with ``responses``; inputs are written in the exact shape of the real D1/D2/D3
sources (see tests/zc_testkit/raw_data.py). Nothing here touches the network.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
import requests
import responses
from shapely.geometry import Point, Polygon, box
from zc_testkit import raw_data as rd

from zipcator import data, data_build as db

SRC = db.Sources(
    zcta_url="https://example.test/zcta.zip",
    county_rel_url="https://example.test/rel.txt",
    geonames_url="https://example.test/US.zip",
    acs_url_template="https://example.test/acs/{year}/acs5",
)


@pytest.fixture
def raw_files(tmp_path: Path) -> dict[str, bytes]:
    return {
        "zcta": rd.boundary_zip_bytes(tmp_path / "mk"),
        "rel": rd.relationship_text().encode("utf-8"),
        "geonames": rd.geonames_zip_bytes(),
    }


def _mock_sources(rsps: responses.RequestsMock, files: dict[str, bytes]) -> None:
    rsps.get(SRC.zcta_url, body=files["zcta"])
    rsps.get(SRC.county_rel_url, body=files["rel"])
    rsps.get(SRC.geonames_url, body=files["geonames"])


# ---------------------------------------------------------------------------------- download
@responses.activate
def test_download_writes_file_and_sidecar_then_reuses_cache(tmp_path: Path) -> None:
    responses.get("https://example.test/f.bin", body=b"hello")
    dest = tmp_path / "raw" / "f.bin"
    assert db.download("https://example.test/f.bin", dest) == dest
    assert dest.read_bytes() == b"hello"
    meta = json.loads((tmp_path / "raw" / "f.bin.meta.json").read_text())
    assert meta["url"] == "https://example.test/f.bin" and meta["bytes"] == 5
    assert meta["downloaded_at"].endswith("Z")
    db.download("https://example.test/f.bin", dest)  # cached
    assert len(responses.calls) == 1


@responses.activate
def test_download_refresh_refetches(tmp_path: Path) -> None:
    responses.get("https://example.test/f.bin", body=b"v1")
    dest = tmp_path / "f.bin"
    db.download("https://example.test/f.bin", dest)
    responses.replace(responses.GET, "https://example.test/f.bin", body=b"v2")
    db.download("https://example.test/f.bin", dest, refresh=True)
    assert dest.read_bytes() == b"v2" and len(responses.calls) == 2


@responses.activate
def test_download_http_error_is_actionable_and_leaves_no_partial_file(tmp_path: Path) -> None:
    responses.get("https://example.test/gone.zip", status=404)
    dest = tmp_path / "gone.zip"
    with pytest.raises(db.BuildError) as exc:
        db.download("https://example.test/gone.zip", dest)
    assert "404" in str(exc.value) and "https://example.test/gone.zip" in str(exc.value)
    assert "build_data.py" in str(exc.value)  # tells the user where to update the URL
    assert not dest.exists() and not list(tmp_path.glob("*.part"))


@responses.activate
def test_download_network_failure_is_actionable(tmp_path: Path) -> None:
    responses.get("https://example.test/x", body=requests.ConnectionError("boom"))
    with pytest.raises(db.BuildError, match="network"):
        db.download("https://example.test/x", tmp_path / "x")
    assert not list(tmp_path.glob("*.part"))


# ---------------------------------------------------------------------------------- readers
def test_read_boundaries_schema_crs_and_land_area(tmp_path: Path) -> None:
    p = tmp_path / "zcta.zip"
    p.write_bytes(rd.boundary_zip_bytes(tmp_path / "mk"))
    gdf = db.read_boundaries(p)
    assert set(gdf["zip"]) == set(rd.ZCTAS)
    assert gdf.crs.to_epsg() == 4326  # NAD83 input converted to WGS84
    assert all(isinstance(z, str) and len(z) == 5 for z in gdf["zip"])
    expected = int(0.1 * 111_000 * 0.1 * 80_000) / rd.SQ_M_PER_SQ_MI
    assert gdf["land_area_sq_mi"].iloc[0] == pytest.approx(expected)


def test_read_boundaries_schema_mismatch_is_actionable(tmp_path: Path) -> None:
    p = tmp_path / "zcta.zip"
    p.write_bytes(rd.boundary_zip_bytes(tmp_path / "mk", drop_column="ALAND20"))
    with pytest.raises(db.BuildError, match="ALAND20"):
        db.read_boundaries(p)


def test_read_boundaries_rejects_html_masquerading_as_zip(tmp_path: Path) -> None:
    p = tmp_path / "zcta.zip"
    p.write_text("<html>moved</html>")
    with pytest.raises(db.BuildError, match="ZIP"):
        db.read_boundaries(p)


def test_relationship_primary_county_state_bom_and_blank_rows(tmp_path: Path) -> None:
    p = tmp_path / "rel.txt"
    p.write_bytes(rd.relationship_text(bom=True).encode("utf-8"))
    rel = db.read_county_relationship(p).set_index("zip")
    assert set(rel.index) == set(rd.ZCTAS)  # blank-ZCTA rows dropped
    assert rel.loc["33602", "county"] == "Hillsborough County"  # largest land overlap
    assert rel.loc["33602", "state"] == "FL"
    assert rel.loc["02108", "state"] == "MA" and rel.loc["59001", "state"] == "MT"
    assert rel.loc["33999", "county"] == "Pinellas County"  # water-only: water tie-break
    assert rel.index.is_unique


def test_relationship_without_bom_also_parses(tmp_path: Path) -> None:
    p = tmp_path / "rel.txt"
    p.write_bytes(rd.relationship_text(bom=False).encode("utf-8"))
    assert len(db.read_county_relationship(p)) == len(rd.ZCTAS)


def test_relationship_schema_mismatch_is_actionable(tmp_path: Path) -> None:
    p = tmp_path / "rel.txt"
    p.write_text("A|B|C\n1|2|3\n")
    with pytest.raises(db.BuildError, match="GEOID_ZCTA5_20"):
        db.read_county_relationship(p)


def test_state_fips_covers_states_dc_pr_and_territories() -> None:
    assert db.STATE_FIPS["01"] == "AL" and db.STATE_FIPS["11"] == "DC"
    assert db.STATE_FIPS["72"] == "PR" and db.STATE_FIPS["15"] == "HI"
    assert db.STATE_FIPS["02"] == "AK"
    assert len(set(db.STATE_FIPS.values())) == len(db.STATE_FIPS)  # no duplicate abbreviations
    assert len(db.STATE_FIPS) >= 56


def test_read_geonames_first_row_per_zip_and_text_zips(tmp_path: Path) -> None:
    p = tmp_path / "US.zip"
    p.write_bytes(rd.geonames_zip_bytes())
    gn = db.read_geonames(p).set_index("zip")
    assert gn.loc["02108", "city"] == "Boston"  # first of two rows
    assert "59001" not in gn.index
    assert gn.index.is_unique


def test_read_geonames_bad_zip_file(tmp_path: Path) -> None:
    p = tmp_path / "US.zip"
    p.write_text("nope")
    with pytest.raises(db.BuildError, match="ZIP"):
        db.read_geonames(p)


# --------------------------------------------------------------------------------- population
@responses.activate
def test_fetch_population_parses_suppressed_values_and_sends_key() -> None:
    responses.get(
        "https://example.test/acs/2023/acs5",
        body=rd.acs_json(),
        content_type="application/json",
    )
    pop = db.fetch_population(year=2023, api_key="K123", acs_url_template=SRC.acs_url_template)
    pop = pop.set_index("zip")
    assert pop.loc["02108", "population"] == 3500
    assert pd.isna(pop.loc["33999", "population"])  # -666666666 = suppressed
    assert str(pop["population"].dtype) == "Int64"
    sent = responses.calls[0].request.url
    assert "key=K123" in sent and "B01003_001E" in sent and "zip" in sent


@responses.activate
def test_fetch_population_missing_key_html_is_actionable() -> None:
    responses.get("https://example.test/acs/2023/acs5", body=rd.MISSING_KEY_HTML)
    with pytest.raises(db.BuildError) as exc:
        db.fetch_population(year=2023, api_key=None, acs_url_template=SRC.acs_url_template)
    assert "CENSUS_API_KEY" in str(exc.value) or "census-api-key" in str(exc.value)


@responses.activate
def test_fetch_population_error_never_leaks_the_key() -> None:
    responses.get("https://example.test/acs/2023/acs5", status=500)
    with pytest.raises(db.BuildError) as exc:
        db.fetch_population(year=2023, api_key="SECRETKEY", acs_url_template=SRC.acs_url_template)
    assert "SECRETKEY" not in str(exc.value)


# ----------------------------------------------------------------------------------- assemble
def _boundaries(geoms: dict[str, object]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"zip": list(geoms), "land_area_sq_mi": [1.0] * len(geoms)},
        geometry=list(geoms.values()),  # type: ignore[arg-type]
        crs="EPSG:4326",
    )


def _rel(zips: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"zip": zips, "state": ["FL"] * len(zips), "county": ["X County"] * len(zips)}
    )


def test_assemble_joins_and_computes_representative_points() -> None:
    donut = Polygon(box(0, 0, 1, 1).exterior.coords, [box(0.25, 0.25, 0.75, 0.75).exterior.coords])
    gdf = db.assemble(
        _boundaries({"11111": donut}),
        _rel(["11111"]),
        pd.DataFrame({"zip": ["11111"], "city": ["Town"]}),
        tolerance=0.0,
    )
    row = gdf.iloc[0]
    assert row.city == "Town" and row.state == "FL" and row.county == "X County"
    assert gdf.geometry.iloc[0].contains(Point(row.intpt_lon, row.intpt_lat))  # inside the ring
    assert list(gdf.columns) == [
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
    assert str(gdf["population"].dtype) == "Int64" and gdf["population"].isna().all()


def test_assemble_tolerates_missing_city_and_drops_rows_without_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    geoms = {"11111": box(0, 0, 1, 1), "22222": box(2, 2, 3, 3)}
    gdf = db.assemble(
        _boundaries(geoms),
        _rel(["11111"]),  # 22222 has no county row -> no state
        pd.DataFrame({"zip": [], "city": []}),
        tolerance=0.0,
    )
    assert gdf["zip"].tolist() == ["11111"] and pd.isna(gdf.iloc[0].city)
    assert "22222" in caplog.text


def test_assemble_simplifies_within_tolerance_and_stays_valid() -> None:
    circle = Point(0, 0).buffer(1.0, quad_segs=500)  # 2000 vertices
    gdf = db.assemble(
        _boundaries({"11111": circle}),
        _rel(["11111"]),
        pd.DataFrame(columns=["zip", "city"]),
        tolerance=0.001,
    )
    simple = gdf.geometry.iloc[0]
    assert len(simple.exterior.coords) < len(circle.exterior.coords) / 4
    assert simple.is_valid
    assert simple.hausdorff_distance(circle) <= 0.0011
    assert simple.area == pytest.approx(circle.area, rel=0.001)


def test_assemble_repairs_invalid_geometry() -> None:
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    gdf = db.assemble(
        _boundaries({"11111": bowtie}),
        _rel(["11111"]),
        pd.DataFrame(columns=["zip", "city"]),
        tolerance=0.0,
    )
    assert gdf.geometry.is_valid.all() and not gdf.geometry.is_empty.any()
    assert gdf.geometry.iloc[0].geom_type in {"Polygon", "MultiPolygon"}


def test_assemble_joins_population() -> None:
    pop = pd.DataFrame({"zip": ["11111"], "population": pd.array([42], dtype="Int64")})
    gdf = db.assemble(
        _boundaries({"11111": box(0, 0, 1, 1)}),
        _rel(["11111"]),
        pd.DataFrame(columns=["zip", "city"]),
        pop,
        tolerance=0.0,
    )
    assert gdf.iloc[0].population == 42


# -------------------------------------------------------------------------------------- build
@responses.activate
def test_build_end_to_end_on_synthetic_inputs(tmp_path: Path, raw_files: dict[str, bytes]) -> None:
    _mock_sources(responses, raw_files)
    out = tmp_path / "data"
    result = db.build(out, sources=SRC, min_rows=len(rd.ZCTAS), tolerance=0.0001)
    assert result.path == out / "zcta.parquet" and result.path.exists()
    gdf = data.load_zctas(result.path)  # also proves the loader accepts what the builder writes
    assert len(gdf) == len(rd.ZCTAS)
    assert "02108" in set(gdf["zip"])  # zero-padded text
    row = gdf.set_index("zip")
    assert row.loc["33602", "county"] == "Hillsborough County" and row.loc["33602", "state"] == "FL"
    assert row.loc["02108", "city"] == "Boston" and pd.isna(row.loc["59001", "city"])
    assert gdf.crs.to_epsg() == 4326 and gdf.geometry.is_valid.all()
    meta = json.loads((out / "metadata.json").read_text())
    assert meta["row_count"] == len(rd.ZCTAS)
    assert meta["simplification_tolerance_deg"] == 0.0001
    assert meta["population_included"] is False
    assert meta["build_script_version"] == db.BUILD_VERSION
    for key in ("built_at", "zipcator_version", "vintages", "sources"):
        assert key in meta
    assert {s["name"] for s in meta["sources"]} == {
        "zcta_boundaries",
        "county_relationship",
        "geonames",
    }
    assert all(s["url"].startswith("https://") and s["downloaded_at"] for s in meta["sources"])
    assert "CC BY 4.0" in meta["vintages"]["geonames"] or "geonames" in meta["vintages"]


@responses.activate
def test_build_is_idempotent_and_reuses_raw_cache(
    tmp_path: Path, raw_files: dict[str, bytes]
) -> None:
    _mock_sources(responses, raw_files)
    out = tmp_path / "data"
    db.build(out, sources=SRC, min_rows=len(rd.ZCTAS))
    n_calls = len(responses.calls)
    first = pd.read_parquet(out / "zcta.parquet")["zip"].tolist()
    db.build(out, sources=SRC, min_rows=len(rd.ZCTAS))
    assert len(responses.calls) == n_calls  # no re-download
    assert pd.read_parquet(out / "zcta.parquet")["zip"].tolist() == first
    db.build(out, sources=SRC, min_rows=len(rd.ZCTAS), refresh=True)
    assert len(responses.calls) == n_calls + 3


@responses.activate
def test_build_with_population(tmp_path: Path, raw_files: dict[str, bytes]) -> None:
    _mock_sources(responses, raw_files)
    responses.get("https://example.test/acs/2023/acs5", body=rd.acs_json())
    out = tmp_path / "data"
    db.build(
        out,
        sources=SRC,
        min_rows=len(rd.ZCTAS),
        with_population=True,
        census_api_key="K",
        acs_year=2023,
    )
    gdf = data.load_zctas(out / "zcta.parquet").set_index("zip")
    assert gdf.loc["02108", "population"] == 3500 and pd.isna(gdf.loc["33999", "population"])
    assert json.loads((out / "metadata.json").read_text())["population_included"] is True


def test_build_with_population_requires_key_before_any_download(tmp_path: Path) -> None:
    with responses.RequestsMock() as rsps:  # any request would raise ConnectionError
        with pytest.raises(db.BuildError, match="CENSUS_API_KEY"):
            db.build(tmp_path, sources=SRC, with_population=True, census_api_key=None)
        assert len(rsps.calls) == 0


@responses.activate
def test_build_without_population_succeeds_when_acs_is_unreachable(
    tmp_path: Path, raw_files: dict[str, bytes]
) -> None:
    """D4 is optional: the default build never calls the ACS API."""
    _mock_sources(responses, raw_files)
    db.build(tmp_path / "d", sources=SRC, min_rows=len(rd.ZCTAS))
    assert all("acs" not in c.request.url for c in responses.calls)


@responses.activate
def test_build_fails_validation_with_default_row_minimum(
    tmp_path: Path, raw_files: dict[str, bytes]
) -> None:
    _mock_sources(responses, raw_files)
    with pytest.raises(db.BuildError, match="33,000"):
        db.build(tmp_path / "d", sources=SRC)
    assert not (tmp_path / "d" / "zcta.parquet").exists()  # nothing half-written is published


@responses.activate
def test_build_replaces_an_existing_artifact_atomically(
    tmp_path: Path, raw_files: dict[str, bytes]
) -> None:
    _mock_sources(responses, raw_files)
    out = tmp_path / "d"
    out.mkdir()
    (out / "zcta.parquet").write_text("old garbage")
    db.build(out, sources=SRC, min_rows=len(rd.ZCTAS))
    assert len(data.load_zctas(out / "zcta.parquet")) == len(rd.ZCTAS)
    assert not list(out.glob("*.tmp"))


@responses.activate
def test_build_network_failure_propagates_as_build_error(tmp_path: Path) -> None:
    responses.get(SRC.zcta_url, body=requests.ConnectionError("offline"))
    with pytest.raises(db.BuildError, match="network"):
        db.build(tmp_path / "d", sources=SRC)


def test_default_sources_are_the_official_census_and_geonames_urls() -> None:
    d = db.DEFAULT_SOURCES
    assert (
        d.zcta_url == "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip"
    )
    assert d.county_rel_url.startswith(
        "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
    )
    assert d.county_rel_url.endswith("tab20_zcta520_county20_natl.txt")
    assert d.geonames_url == "https://download.geonames.org/export/zip/US.zip"
    assert d.acs_url_template.startswith("https://api.census.gov/data/")


def test_land_area_conversion_constant() -> None:
    assert math.isclose(db.SQ_M_PER_SQ_MI, 2_589_988.110336)
