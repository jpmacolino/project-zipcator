"""Everything the Streamlit app needs that is not widget wiring (docs/SPEC.md §5.4, §7).

``app/streamlit_app.py`` only lays out widgets and calls these functions, so nearly all of the
behaviour is unit-testable without a browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

import geopandas as gpd

from zipcator import config, data, export
from zipcator.models import GeocodeResult, SearchResult
from zipcator.search import find_zips

MIN_RADIUS_MI = 0.5
MAX_RADIUS_MI = 100.0
RADIUS_STEP_MI = 0.5
DEFAULT_RADIUS_MI = 10.0
DEFAULT_MIN_OVERLAP_PCT = 0.0

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class AppData:
    """The loaded ZIP boundaries plus build metadata.

    ``token`` identifies this data version (the build timestamp); it keys the search cache so a
    rebuilt artifact never serves stale results.
    """

    zctas: gpd.GeoDataFrame
    metadata: dict[str, Any] | None
    token: str


def load_app_data() -> AppData:
    """Load the artifact. Raises :class:`zipcator.data.ZctaDataError` if it is missing or bad."""
    zctas = data.load_zctas()
    metadata = data.load_metadata()
    built = (metadata or {}).get("built_at")
    token = built if isinstance(built, str) else str(config.zcta_path().stat().st_mtime_ns)
    return AppData(zctas=zctas, metadata=metadata, token=token)


def data_error_help(exc: data.ZctaDataError) -> str:
    """Markdown shown instead of a stack trace when the data is missing or unusable."""
    if isinstance(exc, data.DataNotFoundError):
        return (
            "**The ZIP boundary data has not been built yet.**\n\n"
            "Run these two commands once in the project folder, then reload this page:\n\n"
            "```\nuv sync\nuv run python scripts/build_data.py\n```\n\n"
            "The build downloads the public Census and GeoNames files (about 70 MB) and takes "
            f"about a minute.\n\nDetails: {exc}"
        )
    return (
        "**The ZIP boundary data could not be used.**\n\n"
        f"{exc}\n\n"
        "Rebuild it, then reload this page:\n\n"
        "```\nuv run python scripts/build_data.py --refresh\n```"
    )


def zcta_note() -> str:
    """The one-sentence 'ZIP = ZCTA' explanation required in the UI and README (SPEC §3)."""
    return (
        "ZIP here means a Census ZIP Code Tabulation Area (ZCTA), so PO-box-only and "
        "single-organization ZIP codes, which have no area, are not included"
        "."
    )


def footer_markdown(metadata: dict[str, Any] | None, nominatim_enabled: bool | None = None) -> str:
    """Data sources, the GeoNames CC BY 4.0 attribution, the ZCTA note and the build date.

    The geocoder sentence only mentions the Nominatim fallback when it is actually enabled
    (``ZIPCATOR_DISABLE_NOMINATIM`` turns it off); map tiles are OpenStreetMap either way.
    """
    if nominatim_enabled is None:
        nominatim_enabled = not config.nominatim_disabled()
    built = (metadata or {}).get("built_at")
    date = built[:10] if isinstance(built, str) and _DATE_RE.match(built) else "unknown"
    if nominatim_enabled:
        geocoding = (
            "Addresses are located with the US Census Geocoder, falling back to OpenStreetMap "
            "Nominatim; map tiles and fallback results © "
        )
    else:
        geocoding = "Addresses are located with the US Census Geocoder; map tiles © "
    return (
        "**Data:** US Census Bureau 2020 ZIP Code Tabulation Areas (cartographic boundaries) and "
        "ZCTA-to-county relationship file (public domain); place names from "
        "[GeoNames](https://www.geonames.org), licensed "
        "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). "
        f"{geocoding}"
        "[OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL).\n\n"
        f"{zcta_note()}\n\n"
        f"Data built: {date}."
    )


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]()<>!#~$|:&])")


def markdown_safe(text: str) -> str:
    """Backslash-escape markdown markup so text from a geocoder renders as literal text.

    Without this an address such as ``![x](http://host/pixel.png)`` becomes an image the browser
    fetches (a tracking-pixel vector), and ``**`` / backticks / ``_`` mangle the line. Escaping an
    ASCII punctuation character is always valid CommonMark, and the set is chosen so ordinary
    addresses (letters, digits, spaces, commas, periods) are unchanged.
    """
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _label(located: GeocodeResult) -> str:
    """The matched address as markdown-safe text. Labels generated here (ZIP, coordinates) are
    trusted and keep their parentheses; anything a geocoder returned is escaped."""
    if located.source in ("zip", "coordinates"):
        return located.matched_address
    return markdown_safe(located.matched_address)


def center_caption(located: GeocodeResult) -> str:
    """'Center: …' line; a ZIP-only search is labelled as an approximate center (SPEC §5.1)."""
    if located.source == "coordinates":
        return f"Center: {_label(located)} (coordinates)"
    return f"Center: {_label(located)}"


def summary_markdown(result: SearchResult, located: GeocodeResult) -> str:
    """The bold one-sentence summary, with the geocoder's address made markdown-safe."""
    return f"**{export.summary_line(result, _label(located))}**"


def candidate_options(located: GeocodeResult) -> list[str]:
    """Numbered labels for the match picker; empty unless the address is ambiguous."""
    if len(located.candidates) < 2:
        return []
    return [f"{i}. {c.matched_address}" for i, c in enumerate(located.candidates, start=1)]


def pick_candidate(located: GeocodeResult, index: int) -> GeocodeResult:
    """Re-centre on candidate ``index``; out-of-range or unambiguous input returns ``located``."""
    if len(located.candidates) < 2 or not 0 <= index < len(located.candidates):
        return located
    chosen = located.candidates[index]
    return replace(
        located,
        lat=chosen.lat,
        lon=chosen.lon,
        matched_address=chosen.matched_address,
        source=chosen.source,
    )


def run_search(
    zctas: gpd.GeoDataFrame,
    center: tuple[float, float],
    radius_mi: float,
    min_overlap_pct: float,
) -> SearchResult:
    """``find_zips`` with slider float noise removed (10.000000000000002 → 10.0)."""
    return find_zips(zctas, center, round(float(radius_mi), 6), round(float(min_overlap_pct), 6))
