"""The interactive map (docs/SPEC.md §5.4 'Map'), built with folium.

* The radius is drawn from the **geodesic polygon** that the search used, never a ``folium.Circle``
  (a spherical-mercator circle of ``radius`` metres is a different shape, so map and math would
  disagree: acceptance criterion §9.5).
* ZIP polygons are filled on a sequential scale by overlap %, with a tooltip of ZIP, city and
  overlap; the ZIP containing the center gets a heavier outline.
* ZIPs hidden by the minimum-overlap filter can be shown as grey outlines.
* The view fits the circle.
"""

from __future__ import annotations

import html
from typing import Any

import folium
import shapely
from branca.colormap import LinearColormap
from shapely.geometry import Polygon, mapping
from shapely.geometry.base import BaseGeometry

from zipcator.export import display_overlap
from zipcator.models import SearchResult

#: Sequential ColorBrewer YlGnBu: light = barely touches, dark = mostly inside the radius.
_COLORS = ["#ffffcc", "#a1dab4", "#41b6c4", "#2c7fb8", "#253494"]
_CIRCLE_COLOR = "#d62728"
_GREY = "#808080"
#: Coordinates are rounded to 1e-5 degrees (~1 m) before going into the page: a full-precision
#: float repr is ~15 digits per number and a 100-mile metro search has ~100k of them.
_GRID = 1e-5


def ring_locations(polygon: Polygon) -> list[tuple[float, float]]:
    """Exterior ring as folium wants it: ``(lat, lon)`` pairs (shapely stores ``(lon, lat)``)."""
    return [(y, x) for x, y in polygon.exterior.coords]


def overlap_colormap() -> LinearColormap:
    return LinearColormap(
        _COLORS, vmin=0, vmax=100, caption="Overlap: % of each ZIP's area inside the radius"
    )


def _legend_html() -> str:
    """A small fixed legend (top-right). branca's own legend is a large SVG that covers the map
    and its labels."""
    stops = ", ".join(f"{colour} {i * 25}%" for i, colour in enumerate(_COLORS))
    return (
        '<div style="position:absolute;top:10px;right:10px;z-index:9999;'
        "background:rgba(255,255,255,0.92);padding:6px 10px;border-radius:6px;"
        "box-shadow:0 1px 4px rgba(0,0,0,0.3);font:12px/1.3 system-ui,sans-serif;color:#222;"
        'pointer-events:none">'
        '<div style="font-weight:600;margin-bottom:4px">Overlap with the radius</div>'
        f'<div style="height:10px;width:170px;background:linear-gradient(to right,{stops})"></div>'
        '<div style="display:flex;justify-content:space-between;width:170px">'
        "<span>0%</span><span>50%</span><span>100%</span></div>"
        '<div style="margin-top:3px;color:#555">share of each ZIP inside the circle</div></div>'
    )


def _rounded(geom: BaseGeometry) -> BaseGeometry:
    out = shapely.set_precision(geom, _GRID)
    return geom if out.is_empty else out


def _clean_text(value: object) -> str:
    """HTML-escaped text for tooltips: GeoJSON tooltip values are inserted as HTML."""
    return "" if value is None or value != value else html.escape(str(value), quote=True)


def _feature_collection(frame: Any, *, with_overlap: bool) -> dict[str, Any]:
    features = []
    for row in frame.itertuples(index=False):
        pct = float(row.overlap_pct)
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(_rounded(row.geometry)),
                "properties": {
                    "zip": str(row.zip),
                    "city": _clean_text(row.city),
                    "overlap_pct": pct,
                    "overlap_label": f"{display_overlap(pct):.1f}%",
                    "is_home": bool(row.contains_center),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def build_map(
    result: SearchResult, center_label: str, *, show_excluded: bool = False
) -> folium.Map:
    """Build the folium map for a :class:`SearchResult`."""
    lat, lon = result.center
    cmap = overlap_colormap()
    m = folium.Map(location=[lat, lon], zoom_start=10, tiles="OpenStreetMap", control_scale=True)

    if result.count:
        folium.GeoJson(
            _feature_collection(result.matches, with_overlap=True),
            name="ZIP codes overlapping the radius",
            style_function=lambda feature: {
                "fillColor": cmap(feature["properties"]["overlap_pct"]),
                "fillOpacity": 0.6,
                "color": "#111111" if feature["properties"]["is_home"] else "#555555",
                "weight": 3 if feature["properties"]["is_home"] else 1,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["zip", "city", "overlap_label"],
                aliases=["ZIP", "City", "Overlap"],
                sticky=True,
            ),
        ).add_to(m)

    if show_excluded and len(result.excluded):
        folium.GeoJson(
            _feature_collection(result.excluded, with_overlap=True),
            name="ZIP codes below the minimum overlap",
            style_function=lambda _feature: {
                "fillOpacity": 0,
                "color": _GREY,
                "weight": 2,
                "dashArray": "6 5",
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["zip", "city", "overlap_label"],
                aliases=["ZIP (hidden)", "City", "Overlap"],
                sticky=True,
            ),
        ).add_to(m)

    folium.Polygon(
        locations=ring_locations(result.circle),
        color=_CIRCLE_COLOR,
        weight=3,
        fill=True,
        fill_color=_CIRCLE_COLOR,
        fill_opacity=0.03,
        tooltip=f"{result.radius_mi:g}-mile radius",
    ).add_to(m)
    folium.Marker(
        [lat, lon],
        tooltip=html.escape(center_label, quote=True),
        icon=folium.Icon(color="red", icon="map-marker", prefix="fa"),
    ).add_to(m)
    root: Any = m.get_root()  # a folium Figure; its stubs do not declare .html
    root.html.add_child(folium.Element(_legend_html()))

    minx, miny, maxx, maxy = result.circle.bounds
    m.fit_bounds([[miny, minx], [maxy, maxx]])
    return m
