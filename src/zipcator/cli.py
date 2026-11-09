"""Command-line interface (docs/SPEC.md §5.5).

    zipcator "401 E Jackson St, Tampa, FL 33602" --radius 10 [--min-overlap 5] [--csv out.csv]

Reuses the library exactly as the Streamlit app does. Printing is allowed here (this is the
entry point); library modules only log.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import math
import os
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TextIO

import geopandas as gpd

from zipcator import data, export, geo
from zipcator import geocode as gc
from zipcator.search import find_zips

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = (
    1  # not found / invalid or non-US location / geocoder down / unsupported circle / write error
)
EXIT_NO_DATA = 3  # ZIP boundary data missing or unreadable: run scripts/build_data.py
EXIT_INTERRUPTED = 130  # Ctrl-C, the shell convention (128 + SIGINT)
# Exit status 2 (invalid arguments) is produced by argparse itself.

MIN_RADIUS_MI = 0.5
MAX_RADIUS_MI = 100.0

_EPILOG = """\
exit status:
  0    success (including "no ZIP codes found")
  1    address not found / invalid or non-US location / geocoding service unavailable /
       could not write the CSV / unexpected error
  2    invalid command-line arguments
  3    ZIP boundary data missing or unreadable (run: uv run python scripts/build_data.py)
  130  interrupted (Ctrl-C)

A negative first number looks like an option: put -- before it (options go before the --), e.g.
  zipcator -r 5 -- "-14.27,-170.7"

"ZIP" means a Census ZIP Code Tabulation Area (ZCTA); PO-box-only and unique ZIPs have no
polygon and are not included."""

_NEGATIVE_HINT = (
    "hint: a negative first number looks like an option; put -- before it, e.g. "
    'zipcator -- "-14.27,-170.7"'
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        if "required" in message and "address" in message:
            message += f"\n{_NEGATIVE_HINT}"
        super().error(message)


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


def _csv_path(text: str) -> str:
    if not text.strip():
        raise argparse.ArgumentTypeError("--csv needs a file name")
    return text


def _parser() -> argparse.ArgumentParser:
    p = _Parser(
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
    p.add_argument(
        "--csv", type=_csv_path, metavar="FILE", help="also write all columns to FILE as CSV"
    )
    p.add_argument(
        "--excel", action="store_true",
        help='with --csv: Excel-safe file (ZIPs as ="02108" so Excel keeps leading zeros, '
        "UTF-8 BOM for accents)",
    )  # fmt: skip
    p.add_argument(
        "--zips", action="store_true",
        help="print only a copy-paste ZIP list on stdout (summary goes to stderr); for scripts "
        "and ad tools",
    )  # fmt: skip
    p.add_argument("--newline", action="store_true", help="with --zips: one ZIP per line")
    p.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    return p


# --------------------------------------------------------------------------- plumbing
def _harden_stream(stream: TextIO | None) -> None:
    """Make printing unable to crash on odd characters, and keep piped output LF-only.

    A redirected Windows stdout is cp1252: a Nominatim label with a character outside it
    (macron, okina, CJK, emoji) would raise ``UnicodeEncodeError``. Unencodable characters are
    replaced instead. When not a terminal, newlines are ``\\n`` (not CRLF) so ``--zips --newline``
    works in ``while read`` loops.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        if stream is not None and stream.isatty():
            reconfigure(errors="replace")
        else:
            reconfigure(errors="replace", newline="\n")
    except (ValueError, OSError):  # e.g. a closed stream: leave it alone
        pass


@contextlib.contextmanager
def _logging_to_stderr(verbose: bool) -> Iterator[None]:
    """Scope a stderr handler on the ``zipcator`` logger to one ``main()`` call.

    Not ``logging.basicConfig``: that is a no-op after its first call (so ``-v`` on a second
    call was ignored) and mutates the root logger of whoever embeds this function. The handler
    and level are restored afterwards, so it never outlives the stream it writes to.
    """
    log = logging.getLogger("zipcator")
    previous_level = log.level
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO if verbose else logging.WARNING)
    try:
        yield
    finally:
        log.removeHandler(handler)
        log.setLevel(previous_level)


def _write_atomic(path: Path, payload: bytes) -> None:
    """Write via a temp file in the same directory, so a failure never truncates ``path``."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(payload)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _silence_stdout() -> None:
    """After EPIPE, stop Python's exit-time flush from raising again (the `| head` case)."""
    with contextlib.suppress(AttributeError, OSError, ValueError):
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


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
    _harden_stream(sys.stdout)
    _harden_stream(sys.stderr)
    parser = _parser()
    args = parser.parse_args(argv)  # exits with status 2 on invalid arguments
    if args.excel and not args.csv:
        parser.error("--excel only makes sense together with --csv")
    if args.newline and not args.zips:
        parser.error("--newline only makes sense together with --zips")
    with _logging_to_stderr(args.verbose):
        try:
            return _run(args, geocoder, zctas)
        except BrokenPipeError:  # e.g. `zipcator ... | head -1`
            _silence_stdout()
            return EXIT_OK
        except KeyboardInterrupt:
            print("Interrupted", file=sys.stderr)
            return EXIT_INTERRUPTED
        except Exception as exc:  # last resort: never show a raw traceback unless asked
            logger.info("unexpected error", exc_info=True)  # the traceback shows with -v only
            return _fail(
                f"unexpected failure ({type(exc).__name__}: {exc}). Re-run with -v for details.",
                EXIT_ERROR,
            )


def _run(
    args: argparse.Namespace, geocoder: gc.Geocoder | None, zctas: gpd.GeoDataFrame | None
) -> int:
    try:
        gc.parse_input(args.address)  # fail fast on bad input, before the ~0.5 s data load
    except gc.GeocodeError as exc:
        return _fail(str(exc), EXIT_ERROR)

    if zctas is None:
        try:
            zctas = data.load_zctas()
        except data.ZctaDataError as exc:
            return _fail(str(exc), EXIT_NO_DATA)
    logger.info("loaded %s ZIP boundaries", f"{len(zctas):,}")
    if geocoder is None:
        geocoder = gc.default_geocoder()

    try:
        located = gc.locate(args.address, geocoder, zctas)
        logger.info("center: %s (%s)", located.matched_address, located.source)
        result = find_zips(
            zctas, (located.lat, located.lon), args.radius, min_overlap_pct=args.min_overlap
        )
    except (gc.GeocodeError, geo.AntimeridianError) as exc:
        return _fail(str(exc), EXIT_ERROR)

    wrote = ""
    if args.csv:  # written before any printing, so a closed pipe cannot lose the file
        path = Path(args.csv)
        try:
            _write_atomic(path, export.csv_bytes(result, excel_safe=args.excel))
        except OSError as exc:
            return _fail(f"Could not write {path}: {exc}", EXIT_ERROR)
        wrote = f"Wrote {result.count} row(s) to {path}"

    summary = export.summary_line(result, located.matched_address)
    if args.zips:  # stdout carries nothing but the list; everything else goes to stderr
        print(summary, file=sys.stderr)
        if result.count:
            print(export.zip_list(result.matches["zip"], newline=args.newline))
        if wrote:
            print(wrote, file=sys.stderr)
    else:
        print(summary)
        if result.count:
            print()
            print(export.result_table(result).to_string(index=False, na_rep=""))
        if wrote:
            print(f"\n{wrote}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
