"""The spatial-index prefilter is used and never drops an overlapping ZIP (QA phase 1 #M1)."""

from __future__ import annotations

import logging

import pytest
from shapely.geometry import box
from zc_testkit.synth import CENTERS, MILE_M, TAMPA, make_gdf

from zipcator.search import find_zips

R10 = 10 * MILE_M


def test_prefilter_discards_far_zips_before_the_expensive_work(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression guard: bypassing the sindex would project/intersect all 301 ZIPs."""
    zones = {"33601": box(-1000, -1000, 1000, 1000)}
    for i in range(300):  # a ring of far-away ZIPs, > 200 km from the center
        x = 200_000 + (i % 20) * 3000
        y = (i // 20) * 3000
        zones[f"{90000 + i:05d}"] = box(x, y, x + 2000, y + 2000)
    gdf = make_gdf(zones)
    with caplog.at_level(logging.DEBUG, logger="zipcator.search"):
        res = find_zips(gdf, TAMPA, 10)
    assert res.count == 1
    assert any("prefilter: 1 of 301" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("name", ["tampa", "fairbanks", "honolulu"])
def test_slivers_at_the_four_extreme_tips_of_the_circle_are_found(name: str) -> None:
    """Thin ZIPs reaching 300 m inside the E/W/N/S extremes must survive the bbox prefilter.

    At Fairbanks (65°N) a degree of longitude is only ~29 mi, so a naive ``lon ± r/69``
    degree box would miss the east/west tips.
    """
    center = CENTERS[name]
    inset = 300.0
    gdf = make_gdf(
        {
            "00001": box(R10 - inset, -50, R10 + 5000, 50),  # east tip
            "00002": box(-R10 - 5000, -50, -R10 + inset, 50),  # west tip
            "00003": box(-50, R10 - inset, 50, R10 + 5000),  # north tip
            "00004": box(-50, -R10 - 5000, 50, -R10 + inset),  # south tip
        },
        center,
    )
    res = find_zips(gdf, center, 10)
    assert sorted(res.matches["zip"]) == ["00001", "00002", "00003", "00004"]
    assert (res.matches["overlap_pct"] > 0).all()
