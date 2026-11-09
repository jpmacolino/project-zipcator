"""Print the golden-scenario verification report (SPEC §8.3, §9.2, §9.3) as Markdown.

Compares ``zipcator.search.find_zips`` on the built artifact with the independent reference in
``tests/zc_testkit/reference.py`` (unsimplified raw Census shapes, a geographiclib circle and a
different equal-area projection), and shows the rural overlap-vs-centroid-only comparison.

Usage: uv run python scripts/verify_golden.py
Needs data/zcta.parquet and the cached raw download in data/raw (run scripts/build_data.py).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from geographiclib.geodesic import Geodesic  # noqa: E402
from zc_testkit import reference  # noqa: E402
from zc_testkit.reference import MILE_M  # noqa: E402

from zipcator import data  # noqa: E402
from zipcator.search import find_zips  # noqa: E402

SCENARIOS = [
    ("1 Dense urban: downtown Tampa, FL", 27.9506, -82.4572, 5.0),
    ("2 Rural: Okeechobee, FL", 27.2439, -80.8298, 10.0),
    ("3 Coastal: Miami Beach, FL", 25.7907, -80.1300, 5.0),
    ("4 State border: Texarkana, AR/TX", 33.4418, -94.0377, 15.0),
    ("5 Tiny radius: Jordan, MT", 47.3178, -106.9111, 0.5),
    ("6 Big radius: Orlando, FL", 28.5383, -81.3792, 100.0),
    ("7a Non-CONUS: Honolulu, HI", 21.3069, -157.8583, 10.0),
    ("7b Non-CONUS: San Juan, PR", 18.4655, -66.1057, 10.0),
    ("8 Leading zeros: Boston, MA", 42.3601, -71.0589, 3.0),
]


def main() -> int:
    zctas = data.load_zctas()
    raw = reference.load_raw()
    print(
        "| scenario | center | radius | ZIPs (artifact / raw) | home ZIP (artifact / raw) | "
        "worst overlap drift | worst area diff | search time |"
    )
    print("|---|---|---|---|---|---|---|---|")
    results = {}
    for name, lat, lon, radius in SCENARIOS:
        start = time.perf_counter()
        res = find_zips(zctas, (lat, lon), radius)
        elapsed = time.perf_counter() - start
        ref = reference.overlaps(raw, lat, lon, radius)
        results[name] = (res, ref, lat, lon, radius)
        got = res.matches.set_index("zip")
        want = ref.set_index("zip")
        both = got.index.intersection(want.index)
        drift = float((got.loc[both, "overlap_pct"] - want.loc[both, "overlap_pct"]).abs().max())
        big = [z for z in both if want.loc[z, "area_sq_mi"] > 0.5]
        area = max(
            abs(got.loc[z, "area_in_radius_sq_mi"] / want.loc[z, "area_sq_mi"] - 1) for z in big
        )
        ref_home = ",".join(want.index[want["contains_center"]]) or "none"
        print(
            f"| {name} | {lat}, {lon} | {radius:g} mi | {res.count} / {len(ref)} | "
            f"{res.home_zip or 'none'} / {ref_home} | {drift:.3f} pp | {area * 100:.2f} % | "
            f"{elapsed * 1000:.0f} ms |"
        )

    print("\n**Rural regression (SPEC §9.3): overlap vs centroid-only**\n")
    print(
        "| scenario | overlap search (ZIPs) | centroid-only (ZIPs whose representative point is "
        "inside the circle) | ZIPs only overlap finds |"
    )
    print("|---|---|---|---|")
    for name in ("2 Rural: Okeechobee, FL", "5 Tiny radius: Jordan, MT"):
        res, _ref, lat, lon, radius = results[name]
        centroid_only = {
            r.zip
            for r in zctas.itertuples()
            if Geodesic.WGS84.Inverse(lat, lon, r.intpt_lat, r.intpt_lon)["s12"] / MILE_M <= radius
        }
        overlap = set(res.matches["zip"])
        missed = sorted(overlap - centroid_only)
        rows = res.matches.set_index("zip")
        detail = ", ".join(
            f"{z} ({rows.loc[z, 'overlap_pct']:.1f}% of its area, rep. point "
            f"{rows.loc[z, 'center_distance_mi']:.1f} mi away)"
            for z in missed
        )
        print(f"| {name} | {sorted(overlap)} | {sorted(centroid_only) or 'none'} | {detail} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
