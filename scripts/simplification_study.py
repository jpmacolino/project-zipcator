"""Choose the simplification parameters empirically (docs/SPEC.md §4.2).

For each configuration, compares ``find_zips`` overlap % on simplified vs *unsimplified* ZCTA
shapes over a set of scenarios, plus artifact size, load time and the total area by which
neighbouring ZIPs overlap each other (the raw Census file has none). Requires the raw
downloads in ``data/raw`` (run ``scripts/build_data.py`` once). Prints a Markdown report for
docs/DECISIONS.md.

Usage: uv run python scripts/simplification_study.py [SPEC ...]
  SPEC is ``p<deg>`` for per-polygon Douglas-Peucker (``shapely.simplify``; the original,
  rejected approach) or ``c<deg>`` for coverage simplification (``shapely.coverage_simplify``;
  what the build uses), e.g. ``c0.0005``. Default: a comparison set.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

from zipcator import config, data
from zipcator import data_build as db
from zipcator.search import find_zips

#: (name, (lat, lon), radius_mi)
SCENARIOS = [
    ("Tampa downtown, 5 mi", (27.9506, -82.4572), 5.0),
    ("Okeechobee FL (rural), 10 mi", (27.2439, -80.8298), 10.0),
    ("Miami Beach, 5 mi", (25.7907, -80.1300), 5.0),
    ("Boston, 3 mi", (42.3601, -71.0589), 3.0),
    ("Honolulu, 10 mi", (21.3069, -157.8583), 10.0),
    ("San Juan PR, 10 mi", (18.4655, -66.1057), 10.0),
    ("Orlando, 100 mi", (28.5383, -81.3792), 100.0),
]
DEFAULT_SPECS = ["p0.0005", "p0.001", "c0.0003", "c0.0005", "c0.001", "c0.0015"]


def simplify(exact: gpd.GeoDataFrame, spec: str) -> gpd.GeoDataFrame:
    kind, tol = spec[0], float(spec[1:])
    geoms = exact.geometry.to_numpy()
    if kind == "p":
        out = shapely.simplify(geoms, tol, preserve_topology=True)
    elif kind == "c":
        out = shapely.coverage_simplify(geoms, tol)
    else:
        raise SystemExit(f"bad spec {spec!r}: use p<deg> or c<deg>")
    simple = exact.copy()
    simple["geometry"] = out
    rep = simple.geometry.representative_point()  # as the build does: after simplification
    simple["intpt_lon"], simple["intpt_lat"] = rep.x, rep.y
    return simple


def neighbour_overlap_km2(gdf: gpd.GeoDataFrame) -> float:
    """Total pairwise overlap area between ZIPs (km², equal-area CONUS Albers)."""
    proj = gdf.geometry.to_crs(5070)
    arr = shapely.make_valid(proj.to_numpy())  # projection can invalidate near-degenerate rings
    left, right = proj.sindex.query(proj, predicate="intersects")
    keep = left < right
    return float(shapely.area(shapely.intersection(arr[left[keep]], arr[right[keep]])).sum() / 1e6)


def _raw_frames() -> tuple[gpd.GeoDataFrame, pd.DataFrame, pd.DataFrame]:
    raw = config.raw_dir()
    boundaries = db.read_boundaries(raw / Path(db.DEFAULT_SOURCES.zcta_url).name)
    rel = db.read_county_relationship(raw / Path(db.DEFAULT_SOURCES.county_rel_url).name)
    geonames = db.read_geonames(raw / Path(db.DEFAULT_SOURCES.geonames_url).name)
    return boundaries, rel, geonames


def _size_mb(gdf: gpd.GeoDataFrame, **options: object) -> float:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "zcta.parquet"
        gdf.to_parquet(path, **options)
        return path.stat().st_size / 1e6


def main(argv: list[str]) -> int:
    specs = argv or DEFAULT_SPECS
    boundaries, rel, geonames = _raw_frames()
    exact = db.assemble(boundaries, rel, geonames, tolerance=0.0)
    truth = {
        name: find_zips(exact, center, radius).matches.set_index("zip")["overlap_pct"]
        for name, center, radius in SCENARIOS
    }
    print(f"unsimplified neighbour overlap: {neighbour_overlap_km2(exact):.3f} km2\n")

    names = " | ".join(n for n, *_ in SCENARIOS)
    print(f"| method | MB snappy | MB zstd-19 | load s | overlap km2 | {names} | worst pp |")
    print("|---|---|---|---|---|" + "---|" * (len(SCENARIOS) + 1))
    for spec in specs:
        simple = simplify(exact, spec)
        mb_snappy = _size_mb(simple)
        mb_zstd = _size_mb(simple, **db.PARQUET_OPTIONS)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zcta.parquet"
            simple.to_parquet(path)
            start = time.perf_counter()
            data.load_zctas(path)
            load_s = time.perf_counter() - start
        cells, worst = [], 0.0
        for name, center, radius in SCENARIOS:
            got = find_zips(simple, center, radius).matches.set_index("zip")["overlap_pct"]
            both = truth[name].index.intersection(got.index)
            drift = float((got[both] - truth[name][both]).abs().max()) if len(both) else 0.0
            worst = max(worst, drift)
            cells.append(f"{drift:.3f}")
        overlap = neighbour_overlap_km2(simple)
        print(
            f"| `{spec}` | {mb_snappy:.1f} | {mb_zstd:.1f} | {load_s:.2f} | {overlap:.1f} | "
            + " | ".join(cells)
            + f" | **{worst:.3f}** |",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
