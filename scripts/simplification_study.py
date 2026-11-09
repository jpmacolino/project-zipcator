"""Choose the simplification parameters empirically (docs/SPEC.md §4.2).

For each configuration, compares ``find_zips`` overlap % on simplified vs *unsimplified* ZCTA
shapes over a set of scenarios, plus artifact size and load time. Requires the raw downloads
in ``data/raw`` (run ``scripts/build_data.py`` once). Prints a Markdown report for
docs/DECISIONS.md.

Usage: uv run python scripts/simplification_study.py [SPEC ...]
  SPEC is ``u<deg>`` for a uniform tolerance (e.g. ``u0.0005``) or ``a<rel>:<min>:<max>`` for
  the size-adaptive scheme (e.g. ``a0.01:0.0001:0.003``). Default: a comparison set.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd

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
DEFAULT_SPECS = ["u0.0002", "u0.0005", "u0.001", "a0.01:0.0001:0.003", "a0.02:0.0001:0.003"]


def parse_spec(spec: str) -> db.Simplification:
    if spec.startswith("u"):
        return db.Simplification.uniform(float(spec[1:]))
    if spec.startswith("a"):
        rel, lo, hi = (float(x) for x in spec[1:].split(":"))
        return db.Simplification(rel=rel, min_deg=lo, max_deg=hi)
    raise SystemExit(f"bad spec {spec!r}: use u<deg> or a<rel>:<min>:<max>")


def _raw_frames() -> tuple[gpd.GeoDataFrame, pd.DataFrame, pd.DataFrame]:
    raw = config.raw_dir()
    boundaries = db.read_boundaries(raw / Path(db.DEFAULT_SOURCES.zcta_url).name)
    rel = db.read_county_relationship(raw / Path(db.DEFAULT_SOURCES.county_rel_url).name)
    geonames = db.read_geonames(raw / Path(db.DEFAULT_SOURCES.geonames_url).name)
    return boundaries, rel, geonames


def main(argv: list[str]) -> int:
    specs = argv or DEFAULT_SPECS
    boundaries, rel, geonames = _raw_frames()
    exact = db.assemble(boundaries, rel, geonames, simplification=None)
    truth = {
        name: find_zips(exact, center, radius).matches.set_index("zip")["overlap_pct"]
        for name, center, radius in SCENARIOS
    }

    header = "| simplification | parquet MB | load s | " + " | ".join(n for n, *_ in SCENARIOS)
    print(header + " |")
    print("|---|---|---|" + "---|" * len(SCENARIOS))
    for spec in specs:
        simple = db.assemble(boundaries, rel, geonames, simplification=parse_spec(spec))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zcta.parquet"
            simple.to_parquet(path)
            mb = path.stat().st_size / 1e6
            start = time.perf_counter()
            data.load_zctas(path)
            load_s = time.perf_counter() - start
        cells = []
        for name, center, radius in SCENARIOS:
            got = find_zips(simple, center, radius).matches.set_index("zip")["overlap_pct"]
            both = truth[name].index.intersection(got.index)
            drift = (got[both] - truth[name][both]).abs().max() if len(both) else float("nan")
            only = len(truth[name].index.symmetric_difference(got.index))
            cells.append(f"{drift:.3f} pp ({only} ZIP diff)")
        print(f"| `{spec}` | {mb:.1f} | {load_s:.2f} | " + " | ".join(cells) + " |", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
