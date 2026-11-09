"""Regression tests for the phase 5 QA review (docs/QA_REPORT.md, phase 5)."""

from __future__ import annotations

import html as htmllib
import json
import re

import folium
import pytest
from shapely.geometry import box, shape
from zc_testkit.synth import MILE_M, TAMPA, make_gdf

from zipcator import mapview, ui
from zipcator.models import GeocodeResult
from zipcator.search import find_zips

LABEL = "401 JACKSON ST, TAMPA, FL, 33602"


def _result():  # type: ignore[no-untyped-def]
    zones = {
        "33602": box(-1000, -1000, 1000, 1000),
        "33603": box(3000, 3000, 6000, 6000),
        "02108": box(0, 14_000, 20_000, 34_000),
    }
    gdf = make_gdf(zones)
    gdf.loc[gdf["zip"] == "33602", "city"] = 'Tampa <b>&</b> "Bay"'
    return find_zips(gdf, TAMPA, 10)


def _html(m: folium.Map) -> str:
    return str(m.get_root().render())


def _layer(m: folium.Map) -> folium.GeoJson:
    return next(c for c in m._children.values() if isinstance(c, folium.GeoJson))


def _features(layer: folium.GeoJson) -> list[dict]:  # type: ignore[type-arg]
    data = layer.data
    return list(data["features"]) if isinstance(data, dict) else list(json.loads(data)["features"])


def _located(source: str, label: str) -> GeocodeResult:
    return GeocodeResult(TAMPA[0], TAMPA[1], label, source, [])


# ============================================================== HIGH: hover tooltips unreachable
def test_the_radius_circle_does_not_capture_the_mouse_so_zip_tooltips_work() -> None:
    """The filled circle sat above the ZIP layer and took every hover: only '5-mile radius'
    ever showed inside it and the ZIP / city / overlap tooltip was unreachable."""
    html = _html(mapview.build_map(_result(), LABEL))
    assert html.count('"className": "zipcator-radius"') == 1  # exactly the circle polygon
    assert "path.zipcator-radius{pointer-events:none !important}" in html  # beats Leaflet CSS
    assert "mile radius`" not in html  # and it carries no tooltip of its own


def test_the_radius_is_stated_in_the_legend_instead_of_a_circle_tooltip() -> None:
    assert "10-mile radius" in _html(mapview.build_map(_result(), LABEL))


# ============================================================ MEDIUM: JS template-literal injection
@pytest.mark.parametrize(
    "label",
    [
        "a`b",
        "a${1+1}b",
        "a\\u00zzb",
        "x`);alert(1);//",
        "</script><script>alert(1)</script>",
    ],
)
def test_marker_label_cannot_break_out_of_the_javascript_template(label: str) -> None:
    """html.escape does not escape backtick, backslash or ${ , and folium puts the marker
    tooltip inside a JS template literal: a backtick blanked the map, ${...} would execute."""
    html = _html(mapview.build_map(_result(), label))
    assert label not in html
    safe = mapview.js_safe(label)
    assert not any(ch in safe for ch in "`$\\<>")
    assert safe in html


def test_js_safe_is_reversible_by_a_browser() -> None:
    nasty = "a`b${c}\\d <b>&\"'"
    safe = mapview.js_safe(nasty)
    assert not any(ch in safe for ch in "`$\\<>\"'")
    assert htmllib.unescape(safe) == nasty  # the user still sees exactly the original text


def test_ordinary_labels_are_unchanged_apart_from_html_escaping() -> None:
    assert mapview.js_safe("401 JACKSON ST, TAMPA, FL, 33602") == "401 JACKSON ST, TAMPA, FL, 33602"


# ============================================================ MEDIUM: test gap on html.escape
def test_city_names_are_html_escaped_in_the_geojson_property_itself() -> None:
    """Removing html.escape survived the old test, because JSON encoding already hides < > &
    in the page source. Check the decoded property value instead."""
    layer = _layer(mapview.build_map(_result(), LABEL))
    cities = {f["properties"]["zip"]: f["properties"]["city"] for f in _features(layer)}
    assert cities["33602"] == "Tampa &lt;b&gt;&amp;&lt;/b&gt; &quot;Bay&quot;"


# ===================================================================== LOW: surviving mutants
def test_fit_bounds_uses_lat_lon_order_of_the_circle_bounds() -> None:
    res = _result()
    html = _html(mapview.build_map(res, LABEL))
    m = re.search(
        r"fitBounds\(\s*\[\[\s*([-\d.]+),\s*([-\d.]+)\],\s*\[\s*([-\d.]+),\s*([-\d.]+)\]", html
    )
    assert m is not None
    south, west, north, east = (float(g) for g in m.groups())
    minx, miny, maxx, maxy = res.circle.bounds
    assert (south, west, north, east) == pytest.approx((miny, minx, maxy, maxx), abs=1e-6)
    assert south < north and west < east and 27 < south < 28 and -83 < west < -82


def test_polygons_keep_one_meter_fidelity_after_rounding() -> None:
    """Rounding to 1e-5 degrees keeps shapes; a coarser grid (~1 km) would visibly distort them."""
    res = _result()
    layer = _layer(mapview.build_map(res, LABEL))
    original = res.matches.set_index("zip").geometry
    for f in _features(layer):
        assert shape(f["geometry"]).hausdorff_distance(original[f["properties"]["zip"]]) < 3e-5


def test_legend_is_present_and_uses_the_colour_scale() -> None:
    html = _html(mapview.build_map(_result(), LABEL))
    assert "Overlap with the radius" in html and "share of each ZIP inside the circle" in html
    assert all(colour in html for colour in mapview._COLORS)


def test_tooltip_overlap_label_follows_the_display_rules_for_tiny_overlaps() -> None:
    sliver = box(-100_000, 10 * MILE_M - 100, 100_000, 10 * MILE_M + 100_000)
    gdf = make_gdf({"33601": box(-1000, -1000, 1000, 1000), "33699": sliver})
    layer = _layer(mapview.build_map(find_zips(gdf, TAMPA, 10), LABEL))
    labels = {f["properties"]["zip"]: f["properties"]["overlap_label"] for f in _features(layer)}
    assert labels["33699"] == "0.1%"  # never "0.0%" for a listed ZIP


def test_result_fill_is_visible_and_the_home_outline_colour_differs() -> None:
    layer = _layer(mapview.build_map(_result(), LABEL))
    feats = {f["properties"]["zip"]: f for f in _features(layer)}
    home, other = layer.style_function(feats["33602"]), layer.style_function(feats["33603"])
    assert home["fillOpacity"] > 0.2 and other["fillOpacity"] > 0.2
    assert home["color"] != other["color"]


# ============================================================== MEDIUM: markdown injection
def test_markdown_safe_leaves_ordinary_addresses_untouched() -> None:
    assert (
        ui.markdown_safe("401 JACKSON ST, TAMPA, FL, 33602") == "401 JACKSON ST, TAMPA, FL, 33602"
    )
    assert ui.markdown_safe("Calle Doña María, San Juan, PR 00901") == (
        "Calle Doña María, San Juan, PR 00901"
    )


def test_markdown_safe_neutralises_every_markup_character() -> None:
    """An address containing markdown rendered as bold / links / images, and the browser
    fetched an injected image URL."""
    nasty = (
        "**BOLD** [click](javascript:alert(1)) ![i](http://127.0.0.1:1/x.png) `c` _it_ ~~s~~ "
        "<img onerror=x> a&b $x$ #h | :smile:"
    )
    safe = ui.markdown_safe(nasty)
    assert re.sub(r"\\(.)", r"\1", safe) == nasty  # reversible: a renderer shows the original
    live = re.sub(r"\\.", "", safe)  # drop escaped pairs; anything left would be live markup
    assert not re.search(r"[`*_\[\]()<>!#~$|:&]", live)


def test_markdown_safe_escapes_backslashes_themselves() -> None:
    assert ui.markdown_safe("a\\*b") == "a\\\\\\*b"


def test_center_caption_escapes_geocoder_labels_but_not_our_own() -> None:
    nasty = ui.center_caption(_located("census", "**x** `y`"))
    assert nasty == "Center: \\*\\*x\\*\\* \\`y\\`"
    assert ui.center_caption(_located("zip", "ZIP 33602 (approximate center)")) == (
        "Center: ZIP 33602 (approximate center)"  # generated by us: parentheses stay as they are
    )
    assert ui.center_caption(_located("coordinates", "27.9506, -82.4572")) == (
        "Center: 27.9506, -82.4572 (coordinates)"
    )


def test_summary_markdown_is_bold_and_escapes_the_label() -> None:
    gdf = make_gdf({"33602": box(-1000, -1000, 1000, 1000)})
    res = ui.run_search(gdf, TAMPA, 10.0, 0.0)
    line = ui.summary_markdown(res, _located("census", "**evil** `x`"))
    assert line.startswith("**1 ZIP code overlaps a 10-mile radius around ")
    assert "\\*\\*evil\\*\\*" in line and "\\`x\\`" in line and line.endswith("**")


# ============================================================== LOW: footer honesty
def test_footer_does_not_claim_a_nominatim_fallback_when_it_is_disabled() -> None:
    meta = {"built_at": "2026-11-08T00:00:00Z"}
    assert "Nominatim" in ui.footer_markdown(meta, nominatim_enabled=True)
    off = ui.footer_markdown(meta, nominatim_enabled=False)
    assert "Nominatim" not in off and "OpenStreetMap" in off  # map tiles still need attribution
    assert "Census Geocoder" in off


def test_footer_follows_the_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZIPCATOR_DISABLE_NOMINATIM", "1")
    assert "Nominatim" not in ui.footer_markdown(None)
    monkeypatch.delenv("ZIPCATOR_DISABLE_NOMINATIM")
    assert "Nominatim" in ui.footer_markdown(None)
