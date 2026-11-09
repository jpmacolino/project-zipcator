"""Tests for zipcator.mapview (SPEC §5.4 'Map', acceptance §9.5).

The circle must be drawn from the geodesic polygon (not a folium ``Circle``) so the map matches
the math; ZIP polygons are coloured by overlap %; the view fits the circle.
"""

from __future__ import annotations

import json
import re

import folium
import pytest
from shapely.geometry import box
from zc_testkit.synth import MILE_M, TAMPA, make_gdf

from zipcator import geo, mapview
from zipcator.models import SearchResult
from zipcator.search import find_zips

LABEL = "401 JACKSON ST, TAMPA, FL, 33602"


def _result(min_overlap: float = 0.0) -> SearchResult:
    zones = {
        "33602": box(-1000, -1000, 1000, 1000),  # home, 100 %
        "33603": box(3000, 3000, 6000, 6000),  # 100 %
        "02108": box(0, 14_000, 20_000, 34_000),  # ~3 %
    }
    gdf = make_gdf(zones)
    gdf.loc[gdf["zip"] == "33602", "city"] = 'Tampa <b>&</b> "Bay"'  # HTML-hostile text
    return find_zips(gdf, TAMPA, 10, min_overlap_pct=min_overlap)


def _html(m: folium.Map) -> str:
    return str(m.get_root().render())


def _geojson_layers(m: folium.Map) -> list[folium.GeoJson]:
    return [c for c in m._children.values() if isinstance(c, folium.GeoJson)]


def _features(layer: folium.GeoJson) -> list[dict]:  # type: ignore[type-arg]
    data = layer.data
    return list(data["features"]) if isinstance(data, dict) else list(json.loads(data)["features"])


# --------------------------------------------------------------------------- the circle
def test_radius_is_drawn_from_the_geodesic_polygon_not_a_folium_circle() -> None:
    m = mapview.build_map(_result(), LABEL)
    children = list(m._children.values())
    assert not any(isinstance(c, folium.Circle | folium.CircleMarker) for c in children)
    polys = [
        c for c in children if isinstance(c, folium.Polygon) and not isinstance(c, folium.GeoJson)
    ]
    assert len(polys) == 1
    ring = polys[0].locations
    assert len(ring) >= 128
    html = _html(m)
    assert "L.polygon" in html and "L.circle(" not in html


def test_circle_vertices_are_the_search_circle_vertices_in_lat_lon_order() -> None:
    res = _result()
    ring = mapview.ring_locations(res.circle)
    expected = [(y, x) for x, y in res.circle.exterior.coords]  # folium wants (lat, lon)
    assert ring == expected
    lat, lon = ring[0]
    assert abs(lat - TAMPA[0]) < 0.5 and abs(lon - TAMPA[1]) < 0.5  # not swapped


def test_every_circle_vertex_is_ten_miles_from_the_center_so_map_and_math_agree() -> None:
    from geographiclib.geodesic import Geodesic

    ring = mapview.ring_locations(_result().circle)
    for lat, lon in ring[:: max(1, len(ring) // 12)]:
        d = Geodesic.WGS84.Inverse(TAMPA[0], TAMPA[1], lat, lon)["s12"] / MILE_M
        assert d == pytest.approx(10.0, abs=1e-3)


# ---------------------------------------------------------------------------- the marker
def test_center_marker_has_the_matched_address_tooltip() -> None:
    m = mapview.build_map(_result(), LABEL)
    markers = [c for c in m._children.values() if isinstance(c, folium.Marker)]
    assert len(markers) == 1
    assert markers[0].location == [TAMPA[0], TAMPA[1]]
    assert LABEL in _html(m)


def test_address_text_is_escaped_in_the_marker_tooltip() -> None:
    nasty = "<script>alert(1)</script> & co"
    html = _html(mapview.build_map(_result(), nasty))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html or "\\u003cscript" in html.lower() or "&amp;" in html


# ----------------------------------------------------------------------------- ZIP layer
def test_result_zips_are_one_geojson_layer_with_tooltips() -> None:
    m = mapview.build_map(_result(), LABEL)
    layers = _geojson_layers(m)
    result_layer = layers[0]
    feats = _features(result_layer)
    assert {f["properties"]["zip"] for f in feats} == {"33602", "33603", "02108"}
    assert all(f["geometry"]["type"] in ("Polygon", "MultiPolygon") for f in feats)
    html = _html(m)
    assert "ZIP" in html and "Overlap" in html


def test_tooltip_fields_are_zip_city_and_overlap() -> None:
    layer = _geojson_layers(mapview.build_map(_result(), LABEL))[0]
    tips = [c for c in layer._children.values() if isinstance(c, folium.GeoJsonTooltip)]
    assert len(tips) == 1 and list(tips[0].fields) == ["zip", "city", "overlap_label"]
    props = _features(layer)[0]["properties"]
    assert set(props) >= {"zip", "city", "overlap_label", "is_home", "overlap_pct"}


def test_html_hostile_city_names_do_not_break_the_page() -> None:
    html = _html(mapview.build_map(_result(), LABEL))
    assert "<b>&</b>" not in html  # raw markup from data must not appear unescaped in the tooltip


def test_overlap_labels_follow_the_display_rules() -> None:
    labels = {
        f["properties"]["zip"]: f["properties"]["overlap_label"]
        for f in _features(_geojson_layers(mapview.build_map(_result(), LABEL))[0])
    }
    assert labels["33602"] == "100.0%"
    assert re.fullmatch(r"\d+\.\d%", labels["02108"])
    assert float(labels["02108"].rstrip("%")) > 0  # a listed ZIP is never shown as 0.0%


def test_home_zip_is_flagged_and_drawn_with_a_heavier_outline() -> None:
    layer = _geojson_layers(mapview.build_map(_result(), LABEL))[0]
    feats = {f["properties"]["zip"]: f for f in _features(layer)}
    assert feats["33602"]["properties"]["is_home"] is True
    assert feats["33603"]["properties"]["is_home"] is False
    home_style = layer.style_function(feats["33602"])
    other_style = layer.style_function(feats["33603"])
    assert home_style["weight"] > other_style["weight"]


def test_fill_colour_gets_darker_as_overlap_increases() -> None:
    layer = _geojson_layers(mapview.build_map(_result(), LABEL))[0]
    feats = {f["properties"]["zip"]: f for f in _features(layer)}
    low = layer.style_function(feats["02108"])["fillColor"]
    high = layer.style_function(feats["33603"])["fillColor"]
    assert low != high

    def lum(hex_colour: str) -> float:
        r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    assert lum(high) < lum(low)  # sequential scale: more overlap = darker


def test_colormap_scale_runs_zero_to_one_hundred_with_a_legend() -> None:
    cmap = mapview.overlap_colormap()
    assert (cmap.vmin, cmap.vmax) == (0, 100)
    assert cmap(0) != cmap(100)
    assert "Overlap" in _html(mapview.build_map(_result(), LABEL))


# --------------------------------------------------------------------- excluded (grey) ZIPs
def test_excluded_zips_are_not_drawn_unless_asked() -> None:
    res = _result(min_overlap=50)
    assert list(res.excluded["zip"]) == ["02108"]
    plain = mapview.build_map(res, LABEL)
    assert len(_geojson_layers(plain)) == 1
    shown = mapview.build_map(res, LABEL, show_excluded=True)
    layers = _geojson_layers(shown)
    assert len(layers) == 2
    grey = {f["properties"]["zip"] for f in _features(layers[1])}
    assert grey == {"02108"}


def test_excluded_zips_are_grey_outlines_with_no_fill() -> None:
    res = _result(min_overlap=50)
    layer = _geojson_layers(mapview.build_map(res, LABEL, show_excluded=True))[1]
    style = layer.style_function(_features(layer)[0])
    assert style["fillOpacity"] == 0 and style["color"].lower() in (
        "#808080",
        "#888888",
        "grey",
        "gray",
    )


def test_show_excluded_with_nothing_excluded_adds_no_empty_layer() -> None:
    m = mapview.build_map(_result(), LABEL, show_excluded=True)
    assert len(_geojson_layers(m)) == 1


# ------------------------------------------------------------------------------- viewport
def test_map_fits_the_bounds_of_the_circle() -> None:
    res = _result()
    m = mapview.build_map(res, LABEL)
    html = _html(m)
    assert "fitBounds" in html
    minx, miny, maxx, maxy = res.circle.bounds
    for value in (miny, minx, maxy, maxx):
        assert f"{value:.4f}"[:6] in html or f"{value:.3f}"[:5] in html


def test_empty_result_still_gives_a_map_with_the_circle_and_marker() -> None:
    far = make_gdf({"99999": box(60_000, 0, 61_000, 1000)})
    res = find_zips(far, TAMPA, 10)
    m = mapview.build_map(res, LABEL)
    assert res.count == 0
    assert _geojson_layers(m) == []
    assert any(isinstance(c, folium.Marker) for c in m._children.values())
    assert "fitBounds" in _html(m)


def test_large_radius_circle_is_still_a_polygon() -> None:
    gdf = make_gdf({"33602": box(-1000, -1000, 1000, 1000)})
    res = find_zips(gdf, TAMPA, 100)
    m = mapview.build_map(res, LABEL)
    assert "L.circle(" not in _html(m)
    assert len(mapview.ring_locations(res.circle)) >= 128
    assert geo.geodesic_circle(*TAMPA, 100).equals(res.circle)


# ---------------------------------------------------------------------------- payload size
def test_geometry_coordinates_are_rounded_to_keep_the_page_small() -> None:
    layer = _geojson_layers(mapview.build_map(_result(), LABEL))[0]
    coords = json.dumps([f["geometry"]["coordinates"] for f in _features(layer)])
    longest = max(len(n.split(".")[1]) for n in re.findall(r"-?\d+\.\d+", coords))
    assert longest <= 5  # 1e-5 degrees is ~1 m; full float repr would be 15 digits
