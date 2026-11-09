"""Tables, CSV, ZIP lists and the summary line for a search result (docs/SPEC.md §5.4).

Pure formatting: no I/O, no Streamlit, so the UI and the CLI share exactly the same output.
ZIPs are always 5-character *text*; the CSV is built with the ``csv`` module (never via pandas
dtype inference) so leading zeros survive.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

from zipcator.models import SearchResult

#: (result column, display header) for the on-screen table, in the order of SPEC §5.4.
TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("zip", "ZIP"),
    ("city", "City"),
    ("county", "County"),
    ("state", "State"),
    ("overlap_pct", "Overlap %"),
    ("area_in_radius_sq_mi", "Area in radius (sq mi)"),
    ("nearest_distance_mi", "Nearest distance (mi)"),
    ("center_distance_mi", "Center distance (mi)"),
    ("contains_center", "Contains address"),
    ("population", "Population"),
    ("est_population_in_radius", "Est. pop in radius"),
)
_ZIP_AREA = ("zip_area_sq_mi", "ZIP area (sq mi)")
_TEXT_COLUMNS = frozenset({"City", "County", "State"})
_ZIP_RE = re.compile(r"[0-9]{5}")
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def result_table(result: SearchResult, *, full: bool = False) -> pd.DataFrame:
    """The result as a display table with friendly headers.

    ``full=False`` is the on-screen table (overlap to 1 decimal, SPEC §5.3; other numbers to 2).
    ``full=True`` is the CSV: every column including ZIP area, 4 decimals. Population columns
    appear only if at least one ZIP has population data.
    """
    m = result.matches
    has_population = bool(m["population"].notna().any())
    dp_pct, dp_other = (4, 4) if full else (1, 2)
    columns: list[tuple[str, str]] = list(TABLE_COLUMNS)
    if full:
        columns.insert(6, _ZIP_AREA)
    if not has_population:
        columns = [c for c in columns if c[0] not in ("population", "est_population_in_radius")]

    data: dict[str, Any] = {}
    for key, header in columns:
        col = m[key]
        if key == "zip":
            data[header] = col.astype(str).tolist()
        elif key == "overlap_pct":
            data[header] = col.round(dp_pct).tolist()
        elif key == "contains_center":
            data[header] = ["Yes" if bool(v) else "No" for v in col]
        elif key == "est_population_in_radius":
            data[header] = col.round(0).astype("Int64")
        elif key == "population":
            data[header] = col.astype("Int64")
        elif key.endswith(("_sq_mi", "_mi")):
            data[header] = col.round(dp_other).tolist()
        else:
            data[header] = col.tolist()
    return pd.DataFrame(data, columns=[h for _, h in columns])


def defuse(text: str) -> str:
    """Neutralise spreadsheet formula injection: a leading ``= + - @`` (or tab/CR) gets a ``'``."""
    return "'" + text if text.startswith(_FORMULA_TRIGGERS) else text


def _cell(value: object, header: str) -> object:
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int) or (hasattr(value, "dtype") and value.dtype.kind in "iu"):
        return int(value)  # type: ignore[call-overload]
    if isinstance(value, float) or (hasattr(value, "dtype") and value.dtype.kind == "f"):
        return float(value)  # type: ignore[arg-type]
    text = str(value)
    return defuse(text) if header in _TEXT_COLUMNS else text


def to_csv(result: SearchResult, *, excel_safe: bool = False) -> str:
    """The full result as CSV text (RFC 4180, CRLF, text fields quoted).

    ZIPs are quoted text, so programs reading with ``dtype=str`` keep leading zeros. Excel,
    however, strips leading zeros even from quoted CSV values; ``excel_safe=True`` writes the ZIP
    column as ``="02108"`` (a text formula Excel displays verbatim) at the cost of making that
    one column awkward for programs. Both variants are offered by the app and the CLI.
    """
    table = result_table(result, full=True)
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_NONNUMERIC, lineterminator="\r\n")
    writer.writerow(table.columns)
    for record in table.itertuples(index=False, name=None):
        row = [_cell(v, h) for v, h in zip(record, table.columns, strict=True)]
        if excel_safe:
            row[0] = f'="{row[0]}"'
        writer.writerow(row)
    return buf.getvalue()


def csv_bytes(result: SearchResult, *, excel_safe: bool = False) -> bytes:
    """``to_csv`` encoded as UTF-8 *with BOM*, which is how Excel recognises accents (Mayagüez)."""
    return to_csv(result, excel_safe=excel_safe).encode("utf-8-sig")


def csv_filename(result: SearchResult) -> str:
    lat, lon = result.center
    return f"zipcator_{lat:.4f}_{lon:.4f}_{result.radius_mi:g}mi.csv"


def zip_list(zips: Iterable[object], *, newline: bool = False) -> str:
    """Copy-paste list for ad platforms: unique 5-digit ZIPs in input order."""
    seen: dict[str, None] = {}
    for z in zips:
        if not isinstance(z, str) or not _ZIP_RE.fullmatch(z):
            raise ValueError(f"ZIP codes must be 5-digit strings, got {z!r}")
        seen.setdefault(z)
    return ("\n" if newline else ", ").join(seen)


def summary_line(result: SearchResult, label: str) -> str:
    """One-sentence summary, e.g. '42 ZIP codes overlap a 10-mile radius around <address>'."""
    n = result.count
    radius = f"{result.radius_mi:g}-mile radius around {label}"
    if n == 0:
        line = f"No ZIP codes overlap a {radius}"
    elif n == 1:
        line = f"1 ZIP code overlaps a {radius}"
    else:
        line = f"{n} ZIP codes overlap a {radius}"
    if result.min_overlap_pct > 0:
        line += (
            f" (showing ZIPs with at least {result.min_overlap_pct:g}% overlap, "
            "plus the ZIP containing the center)"
        )
    total = result.total_est_population
    if total is not None:
        note = "estimated, assumes uniform density"
        if not result.population_complete:
            note += "; partial: not all ZIPs have population data"
        line += f" — about {round(total):,} people ({note})"
    return line
