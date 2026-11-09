"""Streamlit AppTest suite for app/streamlit_app.py (SPEC §8.4).

The data artifact is a tiny synthetic parquet (loaded by the real loader via ZIPCATOR_DATA_DIR),
the geocoder is a fake, and ``streamlit_folium.st_folium`` is replaced by a recorder so the map
that *would* be drawn can be inspected. Nothing touches the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import folium
import pytest
import streamlit as st
import streamlit_folium
from shapely.geometry import box
from streamlit.testing.v1 import AppTest
from zc_testkit.synth import TAMPA, make_gdf

from zipcator import geocode as gc
from zipcator.models import GeocodeCandidate, GeocodeResult

APP = Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py"
LABEL = "401 JACKSON ST, TAMPA, FL, 33602"


@dataclass
class FakeGeocoder:
    queries: list[str] = field(default_factory=list)
    mode: str = "ok"

    def geocode(self, query: str) -> GeocodeResult:
        self.queries.append(query)
        if self.mode == "missing":
            raise gc.AddressNotFoundError()
        if self.mode == "down":
            raise gc.GeocodeServiceError()
        if self.mode == "hostile":
            nasty = "**BOLD** [click](http://x.test/p) `tick` ${1+1} <b>"
            cand = GeocodeCandidate(TAMPA[0], TAMPA[1], nasty, "census")
            return GeocodeResult(TAMPA[0], TAMPA[1], nasty, "census", [cand])
        if "main st" in query.lower():
            cands = [
                GeocodeCandidate(TAMPA[0], TAMPA[1], "100 MAIN ST, TAMPA, FL, 33602", "census"),
                GeocodeCandidate(
                    TAMPA[0] + 0.5, TAMPA[1], "100 MAIN ST, OTHERTOWN, FL, 33999", "census"
                ),
            ]
            return GeocodeResult(
                cands[0].lat, cands[0].lon, cands[0].matched_address, "census", cands
            )
        cand = GeocodeCandidate(TAMPA[0], TAMPA[1], LABEL, "census")
        return GeocodeResult(TAMPA[0], TAMPA[1], LABEL, "census", [cand])


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    zones = {
        "33602": box(-1000, -1000, 1000, 1000),  # home, 100 %
        "33603": box(3000, 3000, 6000, 6000),  # 100 %
        "02108": box(0, 14_000, 20_000, 34_000),  # ~3 %, leading zero
    }
    gdf = make_gdf(zones, population={"33602": 3500, "33603": 600, "02108": 10_000})
    gdf.to_parquet(tmp_path / "zcta.parquet")
    (tmp_path / "metadata.json").write_text(
        json.dumps({"built_at": "2026-11-08T23:46:53Z", "row_count": 3}), encoding="utf-8"
    )
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ZIPCATOR_DISABLE_NOMINATIM", raising=False)

    geocoder = FakeGeocoder()
    monkeypatch.setattr(gc, "default_geocoder", lambda *a, **k: geocoder)
    maps: list[SimpleNamespace] = []

    def fake_st_folium(m: folium.Map, **kwargs: object) -> dict[str, object]:
        maps.append(SimpleNamespace(map=m, kwargs=kwargs))
        return {}

    monkeypatch.setattr(streamlit_folium, "st_folium", fake_st_folium)
    st.cache_resource.clear()
    st.cache_data.clear()
    yield SimpleNamespace(geocoder=geocoder, maps=maps, tmp=tmp_path)
    st.cache_resource.clear()
    st.cache_data.clear()


def start() -> AppTest:
    return AppTest.from_file(str(APP), default_timeout=60).run()


def search(at: AppTest, text: str) -> AppTest:
    at.text_input(key="address").input(text)
    at.button[0].click()  # the form's Search button
    return at.run()


def texts(elements) -> list[str]:  # type: ignore[no-untyped-def]
    return [e.value for e in elements]


def table(at: AppTest):  # type: ignore[no-untyped-def]
    assert len(at.dataframe) == 1, "expected exactly one results table"
    return at.dataframe[0].value


def downloads(at: AppTest) -> list[str]:
    return [e.proto.label for e in at.get("download_button")]


# ===================================================================== first load / no results
def test_first_load_shows_the_prompt_the_inputs_and_the_footer(env) -> None:  # type: ignore[no-untyped-def]
    at = start()
    assert not at.exception
    assert at.title[0].value.endswith("Zipcator")
    assert any("press **Search**" in v for v in texts(at.info))
    assert at.text_input(key="address").value == ""
    assert at.number_input(key="radius_num").value == 10.0
    assert at.slider(key="radius_slider").value == 10.0
    assert at.slider(key="min_overlap").value == 0.0
    assert len(at.dataframe) == 0 and env.maps == []
    assert not env.geocoder.queries  # nothing geocoded until Search is pressed


def test_zcta_sentence_is_in_the_page_header(env) -> None:  # type: ignore[no-untyped-def]
    at = start()
    assert any(
        "Census ZIP Code Tabulation Area (ZCTA)" in v and "PO-box" in v for v in texts(at.caption)
    )


def test_footer_has_attribution_and_the_data_build_date(env) -> None:  # type: ignore[no-untyped-def]
    foot = texts(start().caption)[-1]
    assert "GeoNames" in foot and "CC BY 4.0" in foot and "ZCTA" in foot
    assert "Data built: 2026-11-08" in foot
    assert "OpenStreetMap" in foot


# ============================================================================ happy path
def test_address_search_renders_summary_table_map_and_exports(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St, Tampa, FL 33602")
    assert not at.exception and not at.error
    assert env.geocoder.queries == ["401 E Jackson St, Tampa, FL 33602"]
    assert any(
        "3 ZIP codes overlap a 10-mile radius around " + LABEL in v for v in texts(at.markdown)
    )
    assert any(v == f"Center: {LABEL}" for v in texts(at.caption))

    df = table(at)
    assert list(df["ZIP"]) == ["33602", "33603", "02108"]  # home first, then nearest edge
    assert list(df.columns[:5]) == ["ZIP", "City", "County", "State", "Overlap %"]
    assert df["Contains address"].tolist()[0] == "Yes"

    assert len(env.maps) == 1  # the map was drawn exactly once
    drawn = env.maps[0]
    assert isinstance(drawn.map, folium.Map)
    assert drawn.kwargs["returned_objects"] == []  # panning the map must not rerun the app
    assert "L.polygon" in drawn.map.get_root().render()

    assert downloads(at) == ["⬇ Download CSV for Excel", "⬇ Download CSV (plain, for scripts)"]


def test_zips_in_the_table_are_text_with_leading_zeros(env) -> None:  # type: ignore[no-untyped-def]
    df = table(search(start(), "401 E Jackson St"))
    assert all(isinstance(z, str) and len(z) == 5 for z in df["ZIP"])
    assert "02108" in set(df["ZIP"])


def test_population_columns_and_the_uniform_density_caveat(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St")
    assert {"Population", "Est. pop in radius"} <= set(table(at).columns)
    assert any("spread evenly" in v for v in texts(at.caption))
    assert any("people (estimated, assumes uniform density)" in v for v in texts(at.markdown))


# ==================================================================== no keystroke recompute
def test_typing_without_pressing_search_does_not_geocode_or_change_results(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St")
    n_queries, n_maps = len(env.geocoder.queries), len(env.maps)
    at.text_input(key="address").input("something else entirely").run()
    assert len(env.geocoder.queries) == n_queries  # no new geocode
    assert len(at.dataframe) == 1  # old results still shown
    assert any(f"Center: {LABEL}" == v for v in texts(at.caption))
    assert len(env.maps) in (n_maps, n_maps + 1)  # a rerun may redraw, but never re-geocodes


# ================================================================================ failures
def test_geocode_failure_shows_the_message_and_no_results(env) -> None:  # type: ignore[no-untyped-def]
    env.geocoder.mode = "missing"
    at = search(start(), "nowhere in particular")
    assert not at.exception
    assert texts(at.error) == ["Address not found"]
    assert len(at.dataframe) == 0 and env.maps == []


def test_service_down_shows_the_spec_message(env) -> None:  # type: ignore[no-untyped-def]
    env.geocoder.mode = "down"
    at = search(start(), "401 E Jackson St")
    assert texts(at.error) == ["Geocoding service unavailable — try again or enter coordinates"]


def test_a_failed_search_clears_the_previous_results(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St")
    assert len(at.dataframe) == 1
    env.geocoder.mode = "missing"
    at = search(at, "gibberish")
    assert texts(at.error) == ["Address not found"] and len(at.dataframe) == 0


def test_error_persists_across_slider_reruns_until_the_next_search(env) -> None:  # type: ignore[no-untyped-def]
    env.geocoder.mode = "missing"
    at = search(start(), "nowhere")
    at.slider(key="min_overlap").set_value(20.0).run()
    assert texts(at.error) == ["Address not found"]


def test_empty_input_shows_the_prompt_to_enter_something(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "   ")
    assert texts(at.error) and "Enter an address" in texts(at.error)[0]
    assert not at.exception


def test_invalid_coordinates_show_a_clear_error(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "95, 10")
    assert "latitude" in texts(at.error)[0] and not at.exception


def test_non_us_coordinates_say_outside_the_us(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "-33.8688, 151.2093")
    assert texts(at.error) == ["Location is outside the US"]


def test_antimeridian_circle_is_a_friendly_error_not_a_crash(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "52.0, 179.9")
    at.number_input(key="radius_num").set_value(50.0).run()
    assert not at.exception
    assert any("antimeridian" in v for v in texts(at.error))


def test_no_results_is_a_friendly_empty_state_with_the_map_still_drawn(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "27.0, -85.0")  # Gulf of Mexico: inside the US box, no ZIPs
    assert not at.exception and not at.error
    assert any("No ZIP codes overlap" in v for v in texts(at.info))
    assert len(at.dataframe) == 0 and len(env.maps) == 1
    assert downloads(at) == []  # nothing to download


# ============================================================================ missing data
def test_missing_data_shows_build_instructions_not_a_stack_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path / "does-not-exist"))
    st.cache_resource.clear()
    at = start()
    assert not at.exception  # no Python traceback surfaced
    (msg,) = texts(at.error)
    assert "uv run python scripts/build_data.py" in msg and "uv sync" in msg
    assert len(at.text_input) == 0  # the page stopped: no inputs to confuse the user


def test_corrupt_data_tells_the_user_to_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "zcta.parquet").write_text("garbage")
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    st.cache_resource.clear()
    at = start()
    assert not at.exception and "--refresh" in texts(at.error)[0]


def test_app_recovers_after_the_data_is_built(
    env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    empty = tmp_path / "later"
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(empty))
    at = start()
    assert texts(at.error) and not at.text_input
    make_gdf({"33602": box(-1000, -1000, 1000, 1000)}).to_parquet(_mkdir(empty) / "zcta.parquet")
    at = at.run()  # failed loads are not cached: reloading the page works
    assert not at.error and at.text_input(key="address")


def _mkdir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


# ============================================================================ ZIP / lat,lon
def test_zip_only_input_centres_on_the_zcta_without_geocoding(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "33602")
    assert not at.exception and not at.error
    assert env.geocoder.queries == []
    assert any(v == "Center: ZIP 33602 (approximate center)" for v in texts(at.caption))
    assert "33602" in set(table(at)["ZIP"])


def test_leading_zero_zip_input(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "02108")
    assert any("ZIP 02108 (approximate center)" in v for v in texts(at.caption))


def test_unknown_zip_is_a_clear_error(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "99999")
    assert "99999" in texts(at.error)[0] and "boundary" in texts(at.error)[0]


def test_lat_lon_input_is_used_directly(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "27.9506, -82.4572")
    assert env.geocoder.queries == []
    assert any(v == "Center: 27.9506, -82.4572 (coordinates)" for v in texts(at.caption))
    assert table(at)["ZIP"].tolist()[0] == "33602"


# ================================================================================= radius
def test_radius_number_input_and_slider_stay_in_sync(env) -> None:  # type: ignore[no-untyped-def]
    at = start()
    at.number_input(key="radius_num").set_value(25.0).run()
    assert at.slider(key="radius_slider").value == 25.0
    at.slider(key="radius_slider").set_value(40.5).run()
    assert at.number_input(key="radius_num").value == 40.5


def test_radius_range_matches_the_spec(env) -> None:  # type: ignore[no-untyped-def]
    at = start()
    num, slider = at.number_input(key="radius_num"), at.slider(key="radius_slider")
    assert (num.min, num.max, num.step) == (0.5, 100.0, 0.5)
    assert (slider.min, slider.max, slider.step) == (0.5, 100.0, 0.5)


def test_changing_the_radius_after_a_search_updates_results_without_geocoding_again(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St")
    assert len(table(at)) == 3
    at.number_input(key="radius_num").set_value(0.5).run()
    assert len(env.geocoder.queries) == 1  # still just the one lookup
    assert list(table(at)["ZIP"]) == ["33602"]  # at half a mile only the home ZIP remains
    assert any("0.5-mile radius" in v for v in texts(at.markdown))


# =============================================================================== min overlap
def test_min_overlap_slider_changes_the_table_but_keeps_the_home_zip(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St")
    assert len(table(at)) == 3
    at.slider(key="min_overlap").set_value(50.0).run()
    assert list(table(at)["ZIP"]) == ["33602", "33603"]  # 02108 (~3 %) dropped
    assert len(env.geocoder.queries) == 1
    assert any("at least 50% overlap" in v for v in texts(at.markdown))
    at.slider(key="min_overlap").set_value(100.0).run()
    assert list(table(at)["ZIP"]) == ["33602", "33603"]  # fully-inside ZIPs survive 100 %


def test_hidden_zips_appear_as_grey_outlines_only_when_toggled(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St")
    at.slider(key="min_overlap").set_value(50.0).run()
    plain = env.maps[-1].map.get_root().render()
    assert "ZIP (hidden)" not in plain
    at.checkbox(key="show_excluded").check().run()
    shown = env.maps[-1].map.get_root().render()
    assert "ZIP (hidden)" in shown and "#808080" in shown


# ================================================================================= exports
def test_csv_download_buttons_carry_real_csv_with_leading_zeros(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St")
    labels = downloads(at)
    assert labels[0].startswith("⬇ Download CSV for Excel")
    assert labels[1].startswith("⬇ Download CSV (plain")
    protos = {e.proto.label: e.proto for e in at.get("download_button")}
    assert all(p.url for p in protos.values())  # a served file URL was registered


def test_zip_list_is_comma_separated_with_a_newline_option(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St")
    assert at.text_area[0].value == "33602, 33603, 02108"
    at.radio(key="zip_format").set_value("One per line").run()
    assert at.text_area[0].value == "33602\n33603\n02108"


def test_zip_list_follows_the_minimum_overlap_filter(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St")
    at.slider(key="min_overlap").set_value(50.0).run()
    assert at.text_area[0].value == "33602, 33603"


# ============================================================================ candidates
def test_ambiguous_address_offers_a_picker_and_recentres_on_the_choice(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "100 Main St")
    picker = at.selectbox(key="candidate")
    assert [o for o in picker.options] == [
        "1. 100 MAIN ST, TAMPA, FL, 33602",
        "2. 100 MAIN ST, OTHERTOWN, FL, 33999",
    ]
    assert any(v == "Center: 100 MAIN ST, TAMPA, FL, 33602" for v in texts(at.caption))
    picker.select_index(1).run()
    assert any(v == "Center: 100 MAIN ST, OTHERTOWN, FL, 33999" for v in texts(at.caption))
    assert len(env.geocoder.queries) == 1  # choosing a match never geocodes again


def test_unambiguous_address_has_no_picker(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "401 E Jackson St")
    assert len(at.selectbox) == 0


def test_a_new_search_resets_the_picker_to_the_best_match(env) -> None:  # type: ignore[no-untyped-def]
    at = search(start(), "100 Main St")
    at.selectbox(key="candidate").select_index(1).run()
    at = search(at, "100 Main St, again")
    assert at.selectbox(key="candidate").index == 0


# ================================================================================ caching
def test_repeating_the_same_search_reuses_the_cached_result(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from zipcator import ui

    calls: list[tuple[float, float, float, float]] = []
    real = ui.run_search

    def counting(zctas, center, radius, min_overlap):  # type: ignore[no-untyped-def]
        calls.append((*center, radius, min_overlap))
        return real(zctas, center, radius, min_overlap)

    monkeypatch.setattr(ui, "run_search", counting)
    at = search(start(), "401 E Jackson St")
    at.checkbox(key="show_excluded").check().run()  # an unrelated rerun
    at.checkbox(key="show_excluded").uncheck().run()
    assert len(calls) == 1  # find_zips ran once; reruns hit st.cache_data


def test_one_geocoder_instance_serves_every_search(env, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    built: list[int] = []
    shared = env.geocoder
    monkeypatch.setattr(gc, "default_geocoder", lambda *a, **k: (built.append(1), shared)[1])
    st.cache_resource.clear()
    at = search(start(), "401 E Jackson St")
    search(at, "100 Main St")
    assert len(built) == 1  # default_geocoder() builds a fresh cache each call: hold one


def test_each_download_button_gets_its_own_csv_variant(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """AppTest cannot read a download's bytes, so spy on what each button is built from.

    The Excel button must carry the ="02108" variant (with BOM) and the plain button the
    standard one; swapping or duplicating them would silently ship the wrong file.
    """
    from zipcator import export

    real = export.csv_bytes
    calls: list[bool] = []

    def spy(result, *, excel_safe: bool = False):  # type: ignore[no-untyped-def]
        calls.append(excel_safe)
        return real(result, excel_safe=excel_safe)

    monkeypatch.setattr(export, "csv_bytes", spy)
    at = search(start(), "401 E Jackson St")
    assert not at.exception
    assert calls == [True, False]  # Excel-safe first (left button), plain second (right button)


def test_a_hostile_matched_address_is_shown_as_text_and_cannot_break_the_map(env) -> None:  # type: ignore[no-untyped-def]
    """The address comes from a geocoder (Nominatim's display_name is OSM user data). It must
    not become markdown (images, links, bold) and must not escape the map's JS template."""
    env.geocoder.mode = "hostile"
    at = search(start(), "anything")
    assert not at.exception and not at.error
    caption = next(v for v in texts(at.caption) if v.startswith("Center: "))
    summary = next(v for v in texts(at.markdown) if "ZIP code" in v and "overlap" in v)
    for rendered in (caption, summary):
        assert "\\*\\*BOLD\\*\\*" in rendered and "\\[click\\]\\(http" in rendered
        assert "\\`tick\\`" in rendered and "\\<b\\>" in rendered
    html = env.maps[0].map.get_root().render()
    assert "`tick`" not in html and "${1+1}" not in html  # neutralised for folium's JS template
    assert "&#96;tick&#96;" in html and "&#36;{1+1}" in html
