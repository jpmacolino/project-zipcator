"""Build data/zcta.parquet from the official Census + GeoNames sources (docs/SPEC.md §4.3).

Usage:
    uv run python scripts/build_data.py [--refresh] [--with-population] [--census-api-key KEY]

Thin wrapper: all logic lives in ``zipcator.data_build``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from zipcator import config, data_build


class _Formatter(logging.Formatter):
    """Plain progress lines; warnings and errors carry their level so they stand out."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        return message if record.levelno < logging.WARNING else f"{record.levelname}: {message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download and process ZCTA data into data/zcta.parquet."
    )
    parser.add_argument("--refresh", action="store_true", help="re-download cached raw files")
    parser.add_argument(
        "--with-population",
        action="store_true",
        help="add ACS 5-year population (needs a free Census API key)",
    )
    parser.add_argument(
        "--census-api-key",
        default=None,
        help="Census API key (default: the CENSUS_API_KEY environment variable)",
    )
    args = parser.parse_args(argv)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Formatter("%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    try:
        result = data_build.build(
            config.data_dir(),
            refresh=args.refresh,
            with_population=args.with_population,
            census_api_key=args.census_api_key or config.census_api_key(),
        )
    except data_build.BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Done: {result.metadata['row_count']:,} ZCTAs written to {result.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
