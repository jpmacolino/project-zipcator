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
    if not reference.RAW_ZCTA_ZIP.exists():
        print(
            f"ERROR: {reference.RAW_ZCTA_ZIP} not found. Build the data first: "
            "uv run python scripts/build_data.py",
            file=sys.stderr,
        )
        return 1
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

    print("\n**Rural regression (SPEC §9.3): overlap vs a single centre point per ZIP**\n")
    lat, lon = 27.2439, -80.8298
    print("Okeechobee, FL (home ZIP 34972; its neighbour 34974). ZIPs found by each method:\n")
    print(
        "| radius | overlap search | representative point inside the circle | "
        "true polygon centroid inside the circle |"
    )
    print("|---|---|---|---|")
    for radius in (3.0, 5.0, 8.0, 10.0, 15.0, 20.0):
        overlap = sorted(find_zips(zctas, (lat, lon), radius).matches["zip"])
        centre = reference.centre_point_matches(zctas, lat, lon, radius)
        print(
            f"| {radius:g} mi | {overlap} | {sorted(centre['representative point']) or 'none'} | "
            f"{sorted(centre['polygon centroid']) or 'none'} |"
        )
    res = results["2 Rural: Okeechobee, FL"][0].matches.set_index("zip")
    print(
        "\nDistance from the search point to each ZIP's centre point, and its overlap at 10 mi:\n"
    )
    print("| ZIP | overlap at 10 mi | representative point | true centroid |")
    print("|---|---|---|---|")
    cen = zctas.geometry.to_crs(reference.EQUAL_AREA).centroid.to_crs(4326)
    for z in ("34972", "34974"):
        i = zctas.index[zctas["zip"] == z][0]
        rp = Geodesic.WGS84.Inverse(lat, lon, zctas.at[i, "intpt_lat"], zctas.at[i, "intpt_lon"])
        ce = Geodesic.WGS84.Inverse(lat, lon, cen.y[i], cen.x[i])
        print(
            f"| {z} | {res.loc[z, 'overlap_pct']:.1f}% of its area "
            f"({res.loc[z, 'area_in_radius_sq_mi']:.0f} sq mi) | {rp['s12'] / MILE_M:.1f} mi | "
            f"{ce['s12'] / MILE_M:.1f} mi |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
