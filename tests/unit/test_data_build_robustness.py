"""Cache, secret-handling and failure-mode robustness (QA phase 2 LOW findings)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests
import responses
from zc_testkit import raw_data as rd

from zipcator import data
from zipcator import data_build as db

SRC = db.Sources(
    zcta_url="https://example.test/zcta.zip",
    county_rel_url="https://example.test/rel.txt",
    geonames_url="https://example.test/US.zip",
    acs_url_template="https://example.test/acs/{year}/acs5",
)


# --------------------------------------------------------------- corrupted / empty cache files
def test_empty_cached_relationship_file_is_a_build_error_with_refresh_hint(tmp_path: Path) -> None:
    p = tmp_path / "rel.txt"
    p.write_bytes(b"")
    with pytest.raises(db.BuildError, match="--refresh"):
        db.read_county_relationship(p)


def test_html_cached_as_relationship_file_hints_at_refresh(tmp_path: Path) -> None:
    p = tmp_path / "rel.txt"
    p.write_text("<html><body>Service unavailable</body></html>")
    with pytest.raises(db.BuildError) as exc:
        db.read_county_relationship(p)
    assert "--refresh" in str(exc.value) and "GEOID_ZCTA5_20" in str(exc.value)


def test_missing_relationship_file_is_a_build_error(tmp_path: Path) -> None:
    with pytest.raises(db.BuildError, match="could not read"):
        db.read_county_relationship(tmp_path / "nope.txt")


def test_empty_geonames_member_is_a_build_error(tmp_path: Path) -> None:
    import zipfile

    p = tmp_path / "US.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("US.txt", "")
    with pytest.raises(db.BuildError, match="no rows"):
        db.read_geonames(p)


# ------------------------------------------------------------------- stale / foreign cache
@responses.activate
def test_cache_from_a_different_url_is_not_reused(tmp_path: Path) -> None:
    """If a source URL moves but the file name stays, the old bytes must not be trusted."""
    dest = tmp_path / "f.bin"
    responses.get("https://old.example.test/f.bin", body=b"old")
    db.download("https://old.example.test/f.bin", dest)
    responses.get("https://new.example.test/f.bin", body=b"new")
    db.download("https://new.example.test/f.bin", dest)
    assert dest.read_bytes() == b"new"
    meta = json.loads((tmp_path / "f.bin.meta.json").read_text())
    assert meta["url"] == "https://new.example.test/f.bin"


@responses.activate
def test_cache_without_a_sidecar_is_trusted_and_flagged_in_metadata(tmp_path: Path) -> None:
    """Hand-placed raw files are accepted; the metadata says their timestamp is a file mtime."""
    dest = tmp_path / "f.bin"
    dest.write_bytes(b"hand placed")
    db.download("https://example.test/f.bin", dest)  # no request registered: cache hit
    assert len(responses.calls) == 0
    rec = db._source_record("x", "https://example.test/f.bin", dest)
    assert rec["timestamp_source"] == "file_mtime"


def test_source_record_from_sidecar_is_flagged_as_recorded(tmp_path: Path) -> None:
    dest = tmp_path / "f.bin"
    dest.write_bytes(b"abc")
    (tmp_path / "f.bin.meta.json").write_text(
        json.dumps({"url": "u", "downloaded_at": "2020-01-02T03:04:05Z", "bytes": 3})
    )
    assert db._source_record("x", "u", dest)["timestamp_source"] == "download_sidecar"


# ---------------------------------------------------------------- mid-stream download failure
class _FlakyResponse:
    status_code = 200

    def __enter__(self) -> _FlakyResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def iter_content(self, _size: int) -> Iterator[bytes]:
        yield b"first-chunk"
        raise requests.ConnectionError("connection reset mid-stream")


class _FlakySession:
    def get(self, *_a: Any, **_k: Any) -> _FlakyResponse:
        return _FlakyResponse()


def test_download_failing_mid_stream_leaves_no_partial_or_final_file(tmp_path: Path) -> None:
    dest = tmp_path / "raw" / "big.zip"
    with pytest.raises(db.BuildError, match="network"):
        db.download("https://example.test/big.zip", dest, session=_FlakySession())  # type: ignore[arg-type]
    assert not dest.exists()
    assert not list((tmp_path / "raw").glob("*.part"))
    assert not list((tmp_path / "raw").glob("*.meta.json"))


# --------------------------------------------------------------------------- secrets
@responses.activate
def test_census_key_is_absent_from_the_whole_exception_chain() -> None:
    responses.get(
        "https://example.test/acs/2023/acs5",
        body=requests.ConnectionError("boom https://x/?key=SECRETKEY"),
    )
    with pytest.raises(db.BuildError) as exc:
        db.fetch_population(year=2023, api_key="SECRETKEY", acs_url_template=SRC.acs_url_template)
    err = exc.value
    assert "SECRETKEY" not in str(err)
    assert err.__cause__ is None and err.__context__ is None  # nothing carries the raw message
    assert "SECRETKEY" not in repr(err)


# --------------------------------------------------------------------- atomic replacement
@responses.activate
def test_failed_build_leaves_the_previous_artifact_untouched(tmp_path: Path) -> None:
    """Atomicity claim: a build whose validation fails must not damage a good artifact."""
    files = {
        "zcta": rd.boundary_zip_bytes(tmp_path / "mk"),
        "rel": rd.relationship_text().encode(),
        "geonames": rd.geonames_zip_bytes(),
    }
    responses.get(SRC.zcta_url, body=files["zcta"])
    responses.get(SRC.county_rel_url, body=files["rel"])
    responses.get(SRC.geonames_url, body=files["geonames"])
    out = tmp_path / "d"
    db.build(out, sources=SRC, min_rows=len(rd.ZCTAS))
    before_parquet = (out / "zcta.parquet").read_bytes()
    before_meta = (out / "metadata.json").read_bytes()

    with pytest.raises(db.BuildError, match="33,000"):
        db.build(out, sources=SRC)  # default min_rows: tiny synthetic data fails validation
    assert (out / "zcta.parquet").read_bytes() == before_parquet
    assert (out / "metadata.json").read_bytes() == before_meta
    assert not list(out.glob("*.tmp"))
    assert len(data.load_zctas(out / "zcta.parquet")) == len(rd.ZCTAS)


# ------------------------------------------------------ state-straddling ZCTA (real: 137 of them)
def test_zcta_straddling_a_state_line_takes_the_state_of_its_largest_county(
    tmp_path: Path,
) -> None:
    def row(zcta: str, county: str, name: str, land: int) -> str:
        return f"1|{zcta}|Z|0|0|G|B|S|2|{county}|{name}|1|1|G|H|A|{land}|0"

    text = (
        rd.REL_HEADER
        + "\n"
        + "\n".join(
            [
                row("30000", "13001", "Georgia Side County", 4_000_000),
                row("30000", "01001", "Alabama Side County", 6_000_000),  # larger -> wins
                row("30001", "13001", "Georgia Side County", 5_000_000),
                row("30001", "01001", "Alabama Side County", 1_000_000),
            ]
        )
    )
    p = tmp_path / "rel.txt"
    p.write_text(text + "\n", encoding="utf-8")
    rel = db.read_county_relationship(p).set_index("zip")
    assert (rel.loc["30000", "state"], rel.loc["30000", "county"]) == ("AL", "Alabama Side County")
    assert (rel.loc["30001", "state"], rel.loc["30001", "county"]) == ("GA", "Georgia Side County")


def test_relationship_tie_is_broken_deterministically_by_county_geoid(tmp_path: Path) -> None:
    def row(county: str, name: str) -> str:
        return f"1|30000|Z|0|0|G|B|S|2|{county}|{name}|1|1|G|H|A|500|0"

    text = rd.REL_HEADER + "\n" + row("13001", "B County") + "\n" + row("01001", "A County") + "\n"
    p = tmp_path / "rel.txt"
    p.write_text(text, encoding="utf-8")
    assert db.read_county_relationship(p).iloc[0]["county"] == "A County"  # lowest GEOID


def test_blank_city_warning_when_geonames_looks_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty/garbled GeoNames file would silently yield all-blank cities."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("US.txt", "US\t99999\tNowhere\tX\tX\t\t\t\t\t0\t0\t1\n")
    files = {
        "zcta": rd.boundary_zip_bytes(tmp_path / "mk"),
        "rel": rd.relationship_text().encode(),
        "geonames": buf.getvalue(),
    }
    with responses.RequestsMock() as rsps:
        rsps.get(SRC.zcta_url, body=files["zcta"])
        rsps.get(SRC.county_rel_url, body=files["rel"])
        rsps.get(SRC.geonames_url, body=files["geonames"])
        with caplog.at_level("WARNING", logger="zipcator.data_build"):
            db.build(tmp_path / "d", sources=SRC, min_rows=len(rd.ZCTAS))
    assert "no city name" in caplog.text
    assert pd.isna(data.load_zctas(tmp_path / "d" / "zcta.parquet")["city"]).all()
