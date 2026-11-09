"""Tests for zipcator.ui: everything the Streamlit app needs that is not widget wiring.

SPEC §5.4 (missing-data message, footer, center labelling) and §7 ('all logic lives in
src/zipcator; app/streamlit_app.py is layout and wiring only').
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely.geometry import box
from zc_testkit.synth import TAMPA, make_gdf

from zipcator import cli, data, ui
from zipcator.models import GeocodeCandidate, GeocodeResult


def _write_artifact(tmp: Path, *, metadata: dict | None = None) -> None:  # type: ignore[type-arg]
    make_gdf({"33602": box(-1000, -1000, 1000, 1000)}).to_parquet(tmp / "zcta.parquet")
    if metadata is not None:
        (tmp / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


# ----------------------------------------------------------------------------- constants
def test_input_limits_match_the_spec_and_the_cli() -> None:
    assert (ui.MIN_RADIUS_MI, ui.MAX_RADIUS_MI, ui.RADIUS_STEP_MI, ui.DEFAULT_RADIUS_MI) == (
        0.5,
        100.0,
        0.5,
        10.0,
    )
    assert (ui.MIN_RADIUS_MI, ui.MAX_RADIUS_MI) == (cli.MIN_RADIUS_MI, cli.MAX_RADIUS_MI)
    assert ui.DEFAULT_MIN_OVERLAP_PCT == 0.0


# --------------------------------------------------------------------------- data loading
def test_load_app_data_returns_zctas_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_artifact(tmp_path, metadata={"built_at": "2026-11-08T23:46:53Z", "row_count": 1})
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    app_data = ui.load_app_data()
    assert list(app_data.zctas["zip"]) == ["33602"]
    assert app_data.metadata == {"built_at": "2026-11-08T23:46:53Z", "row_count": 1}
    assert app_data.token == "2026-11-08T23:46:53Z"  # keys search caches to this data version


def test_load_app_data_without_metadata_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_artifact(tmp_path)
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    app_data = ui.load_app_data()
    assert app_data.metadata is None and app_data.token


def test_missing_data_raises_the_library_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path / "nope"))
    with pytest.raises(data.DataNotFoundError):
        ui.load_app_data()


def test_missing_data_help_gives_the_exact_commands_and_no_traceback() -> None:
    text = ui.data_error_help(data.DataNotFoundError("ZIP boundary data not found at X"))
    assert "uv run python scripts/build_data.py" in text
    assert "uv sync" in text  # a fresh clone needs setup first
    assert "Traceback" not in text and 'File "' not in text


def test_corrupt_data_help_says_to_rebuild_with_refresh() -> None:
    text = ui.data_error_help(data.DataFormatError("failed validation: 3 row(s) have no state"))
    assert "--refresh" in text and "failed validation" in text
    assert "Traceback" not in text


# --------------------------------------------------------------------------------- footer
def test_footer_has_sources_attribution_zcta_note_and_build_date() -> None:
    text = ui.footer_markdown({"built_at": "2026-11-08T23:46:53Z"})
    assert "2026-11-08" in text
    assert "GeoNames" in text and "CC BY 4.0" in text
    assert "Census" in text and "ZCTA" in text
    assert "OpenStreetMap" in text  # Nominatim fallback + map tiles
    assert "PO-box" in text or "PO box" in text


def test_footer_without_metadata_says_the_date_is_unknown() -> None:
    text = ui.footer_markdown(None)
    assert "unknown" in text.lower() and "GeoNames" in text


def test_footer_survives_a_garbled_build_date() -> None:
    assert "unknown" in ui.footer_markdown({"built_at": 12345}).lower()
    assert "unknown" in ui.footer_markdown({}).lower()


def test_footer_links_the_license_sources() -> None:
    text = ui.footer_markdown({"built_at": "2026-11-08T00:00:00Z"})
    assert "https://creativecommons.org/licenses/by/4.0/" in text
    assert "https://www.geonames.org" in text


def test_zcta_note_is_one_plain_sentence() -> None:
    note = ui.zcta_note()
    assert "ZCTA" in note and "PO-box" in note and note.count(".") == 1
    assert note.startswith("ZIP")


# ---------------------------------------------------------------------------- center label
def _res(source: str, label: str) -> GeocodeResult:
    return GeocodeResult(TAMPA[0], TAMPA[1], label, source, [])


def test_center_caption_for_a_zip_is_labelled_approximate() -> None:
    cap = ui.center_caption(_res("zip", "ZIP 33602 (approximate center)"))
    assert cap == "Center: ZIP 33602 (approximate center)"


def test_center_caption_for_coordinates_and_addresses() -> None:
    assert ui.center_caption(_res("coordinates", "27.9506, -82.4572")) == (
        "Center: 27.9506, -82.4572 (coordinates)"
    )
    assert ui.center_caption(_res("census", "401 JACKSON ST, TAMPA, FL, 33602")) == (
        "Center: 401 JACKSON ST, TAMPA, FL, 33602"
    )


# --------------------------------------------------------------------- multiple candidates
def _multi() -> GeocodeResult:
    cands = [
        GeocodeCandidate(37.4, -102.6, "100 MAIN ST, SPRINGFIELD, CO, 81073", "census"),
        GeocodeCandidate(42.2, -72.5, "100 MAIN ST, SPRINGFIELD, MA, 01151", "census"),
        GeocodeCandidate(39.8, -89.6, "100 MAIN ST, SPRINGFIELD, IL, 62701", "census"),
    ]
    return GeocodeResult(37.4, -102.6, cands[0].matched_address, "census", cands)


def test_candidate_options_numbered_and_only_when_ambiguous() -> None:
    opts = ui.candidate_options(_multi())
    assert opts == [
        "1. 100 MAIN ST, SPRINGFIELD, CO, 81073",
        "2. 100 MAIN ST, SPRINGFIELD, MA, 01151",
        "3. 100 MAIN ST, SPRINGFIELD, IL, 62701",
    ]
    assert ui.candidate_options(_res("census", "x")) == []
    one = GeocodeResult(1.0, 2.0, "A", "census", [GeocodeCandidate(1.0, 2.0, "A", "census")])
    assert ui.candidate_options(one) == []


def test_pick_candidate_recentres_on_the_chosen_match() -> None:
    picked = ui.pick_candidate(_multi(), 1)
    assert (picked.lat, picked.lon) == (42.2, -72.5)
    assert picked.matched_address == "100 MAIN ST, SPRINGFIELD, MA, 01151"
    assert len(picked.candidates) == 3  # still ambiguous: the user can change their mind


def test_pick_candidate_default_and_out_of_range_fall_back_to_the_best_match() -> None:
    base = _multi()
    assert ui.pick_candidate(base, 0).lat == base.lat
    assert ui.pick_candidate(base, 99).lat == base.lat
    assert ui.pick_candidate(base, -1).lat == base.lat
    plain = _res("zip", "ZIP 33602 (approximate center)")
    assert ui.pick_candidate(plain, 0) == plain


# ----------------------------------------------------------------------------- run_search
def test_run_search_is_find_zips_with_validated_inputs() -> None:
    gdf = make_gdf({"33602": box(-1000, -1000, 1000, 1000), "33603": box(3000, 3000, 6000, 6000)})
    res = ui.run_search(gdf, TAMPA, 10.0, 0.0)
    assert list(res.matches["zip"]) == ["33602", "33603"]
    assert res.radius_mi == 10.0


def test_run_search_snaps_slider_floats_to_clean_values() -> None:
    """Sliders can deliver 10.000000000000002; the result and cache key stay tidy."""
    gdf = make_gdf({"33602": box(-1000, -1000, 1000, 1000)})
    res = ui.run_search(gdf, TAMPA, 10.000000000000002, 0.30000000000000004)
    assert res.radius_mi == 10.0 and res.min_overlap_pct == 0.3
