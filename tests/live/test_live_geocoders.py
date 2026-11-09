"""Live geocoder checks (marker ``network``; run manually: ``uv run pytest -m network``).

Deliberately few and polite: two Census requests and exactly one Nominatim request, with the
project's descriptive User-Agent. Expected positions are independent facts (downtown Tampa),
not whatever the service happened to return when the fixtures were recorded.
"""

from __future__ import annotations

import pytest

from zipcator import geo
from zipcator import geocode as gc

pytestmark = pytest.mark.network

DOWNTOWN_TAMPA = (27.9506, -82.4572)


def test_census_geocodes_a_known_downtown_tampa_address() -> None:
    res = gc.CensusGeocoder().geocode("401 E Jackson St, Tampa, FL 33602")
    assert res.source == "census"
    assert "TAMPA" in res.matched_address.upper() and "33602" in res.matched_address
    assert geo.geodesic_distance_mi(res.lat, res.lon, *DOWNTOWN_TAMPA) < 0.5
    assert res.candidates and res.candidates[0].lat == res.lat


def test_census_reports_gibberish_as_not_found() -> None:
    with pytest.raises(gc.AddressNotFoundError):
        gc.CensusGeocoder().geocode("zzzz qqqq nowhere 99999")


def test_nominatim_fallback_geocodes_a_landmark_within_policy() -> None:
    """The single Nominatim request of the suite."""
    res = gc.NominatimGeocoder().geocode("Tampa Theatre, Franklin St, Tampa")
    assert res.source == "nominatim"
    assert geo.geodesic_distance_mi(res.lat, res.lon, *DOWNTOWN_TAMPA) < 1.0
    assert "United States" in res.matched_address


def test_default_geocoder_with_nominatim_disabled_uses_census_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZIPCATOR_DISABLE_NOMINATIM", "1")
    res = gc.default_geocoder().geocode("401 E Jackson St, Tampa, FL 33602")
    assert res.source == "census"
