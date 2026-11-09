"""Command-line interface (docs/SPEC.md §5.5).

    zipcator "401 E Jackson St, Tampa, FL 33602" --radius 10 [--min-overlap 5] [--csv out.csv]

Reuses the library exactly as the Streamlit app does. Printing is allowed here (this is the
entry point); library modules only log.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections.abc import Sequence
from pathlib import Path

import geopandas as gpd

from zipcator import data, export, geo
from zipcator import geocode as gc
from zipcator.search import find_zips

EXIT_OK = 0
EXIT_ERROR = 1  # address not found, outside the US, geocoder down, unsupported circle, write error
EXIT_USAGE = 2  # invalid arguments (argparse's own convention)
EXIT_NO_DATA = 3  # ZIP boundary data missing or unreadable: run scripts/build_data.py

MIN_RADIUS_MI = 0.5
MAX_RADIUS_MI = 100.0

_EPILOG = """\
exit status:
  0  success (including "no ZIP codes found")
  1  address not found / outside the US / geocoding service unavailable / could not write CSV
  2  invalid command-line arguments
  3  ZIP boundary data missing or unreadable (run: uv run python scripts/build_data.py)

"ZIP" means a Census ZIP Code Tabulation Area (ZCTA); PO-box-only and unique ZIPs have no
polygon and are not included."""


def _number(text: str, name: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{name} must be a number (got {text!r})") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"{name} must be a finite number (got {text!r})")
    return value


def _radius(text: str) -> float:
    value = _number(text, "radius")
    if not MIN_RADIUS_MI <= value <= MAX_RADIUS_MI:
        raise argparse.ArgumentTypeError(
            f"radius must be between {MIN_RADIUS_MI:g} and {MAX_RADIUS_MI:g} miles (got {text})"
        )
    return value


def _min_overlap(text: str) -> float:
    value = _number(text, "min-overlap")
    if not 0 <= value <= 100:
        raise argparse.ArgumentTypeError(f"min-overlap must be between 0 and 100 (got {text})")
    return value


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zipcator",
        description="List every US ZIP code (Census ZCTA) whose area overlaps a radius around "
        "an address, ZIP code or 'lat, lon' coordinates.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("address", help="one-line US address, a 5-digit ZIP, or 'lat, lon'")
    p.add_argument(
        "-r", "--radius", type=_radius, default=10.0, metavar="MILES",
        help=f"radius in miles, {MIN_RADIUS_MI:g}-{MAX_RADIUS_MI:g} (default: 10)",
    )  # fmt: skip
    p.add_argument(
        "-m", "--min-overlap", type=_min_overlap, default=0.0, metavar="PCT",
        help="hide ZIPs with less than PCT%% of their area in the radius (default: 0 = any "
        "overlap); the ZIP containing the center is always shown",
    )  # fmt: skip
    p.add_argument("--csv", metavar="FILE", help="also write all columns to FILE as CSV")
    p.add_argument(
        "--excel", action="store_true",
        help="with --csv: write ZIPs as =\"02108\" so Excel keeps leading zeros",
    )  # fmt: skip
    p.add_argument(
        "--zips", action="store_true",
        help="print only a copy-paste ZIP list (summary goes to stderr); for scripts and ad tools",
    )  # fmt: skip
    p.add_argument("--newline", action="store_true", help="with --zips: one ZIP per line")
    p.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    return p


def _fail(message: str, code: int) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return code


def main(
    argv: Sequence[str] | None = None,
    *,
    geocoder: gc.Geocoder | None = None,
    zctas: gpd.GeoDataFrame | None = None,
) -> int:
    """Run the CLI. ``geocoder`` and ``zctas`` can be injected (tests); defaults are real."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.excel and not args.csv:
        parser.error("--excel only makes sense together with --csv")
    if args.newline and not args.zips:
        parser.error("--newline only makes sense together with --zips")
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    if zctas is None:
        try:
            zctas = data.load_zctas()
        except data.ZctaDataError as exc:
            return _fail(str(exc), EXIT_NO_DATA)
    if geocoder is None:
        geocoder = gc.default_geocoder()

    try:
        located = gc.locate(args.address, geocoder, zctas)
        result = find_zips(
            zctas, (located.lat, located.lon), args.radius, min_overlap_pct=args.min_overlap
        )
    except (gc.GeocodeError, geo.AntimeridianError) as exc:
        return _fail(str(exc), EXIT_ERROR)

    summary = export.summary_line(result, located.matched_address)
    if args.zips:
        print(summary, file=sys.stderr)
        if result.count:
            print(export.zip_list(result.matches["zip"], newline=args.newline))
    else:
        print(summary)
        if result.count:
            print()
            print(export.result_table(result).to_string(index=False, na_rep=""))

    if args.csv:
        path = Path(args.csv)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(export.csv_bytes(result, excel_safe=args.excel))
        except OSError as exc:
            return _fail(f"Could not write {path}: {exc}", EXIT_ERROR)
        print(f"\nWrote {result.count} row(s) to {path}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
