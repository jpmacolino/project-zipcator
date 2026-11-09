"""Input parsing and location resolution (SPEC §5.1, §5.2 'input parsing', §8.2 geocode)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from shapely.geometry import box
from zc_testkit.synth import CENTERS, TAMPA, make_gdf

from zipcator import geocode as gc
from zipcator.models import GeocodeCandidate, GeocodeResult


# ---------------------------------------------------------------------------- parse_input
@pytest.mark.parametrize("text", ["33602", "02108", "  33602  ", "00501", "99950"])
def test_bare_zip_is_recognised_and_stays_text(text: str) -> None:
    parsed = gc.parse_input(text)
    assert parsed.kind == "zip" and parsed.zip == text.strip()
    assert isinstance(parsed.zip, str) and len(parsed.zip) == 5


def test_zip_plus_four_is_treated_as_its_zip() -> None:
    parsed = gc.parse_input("02108-1234")
    assert (parsed.kind, parsed.zip) == ("zip", "02108")


@pytest.mark.parametrize(
    ("text", "lat", "lon"),
    [
        ("27.9506, -82.4572", 27.9506, -82.4572),
        ("27.9506,-82.4572", 27.9506, -82.4572),
        ("   27.9506 ,   -82.4572   ", 27.9506, -82.4572),
        ("27.9506 -82.4572", 27.9506, -82.4572),  # whitespace separated
        ("-33.8688, 151.2093", -33.8688, 151.2093),  # negative lat, positive lon
        ("+21.3069, -157.8583", 21.3069, -157.8583),
        ("40, -74", 40.0, -74.0),  # integers
        ("0, 0", 0.0, 0.0),
        ("90, 180", 90.0, 180.0),  # inclusive bounds
        ("-90, -180", -90.0, -180.0),
    ],
)
def test_lat_lon_pairs_are_parsed(text: str, lat: float, lon: float) -> None:
    parsed = gc.parse_input(text)
    assert parsed.kind == "coords"
    assert (parsed.lat, parsed.lon) == (lat, lon)


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("91, 0", "latitude"),
        ("-90.5, 0", "latitude"),
        ("0, 181", "longitude"),
        ("0, -180.01", "longitude"),
        ("100, 200", "latitude"),
        ("12345 67890", "latitude"),  # two numbers: coordinates, and out of range
    ],
)
def test_out_of_range_coordinates_are_rejected_with_a_clear_message(text: str, word: str) -> None:
    with pytest.raises(gc.InvalidInputError, match=word):
        gc.parse_input(text)


def test_invalid_input_error_is_both_a_geocode_error_and_a_value_error() -> None:
    assert issubclass(gc.InvalidInputError, gc.GeocodeError)
    assert issubclass(gc.InvalidInputError, ValueError)


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_empty_input_is_rejected(text: str) -> None:
    with pytest.raises(gc.InvalidInputError, match="Enter"):
        gc.parse_input(text)


@pytest.mark.parametrize(
    "text",
    [
        "401 E Jackson St, Tampa, FL 33602",
        "1600 Pennsylvania Ave NW",
        "123, 456 Main St",  # numbers but also words: an address
        "33602 Tampa",
        "Calle Doña María, San Juan, PR",
        "ZIP 33602",
        "1 Infinite Loop",
    ],
)
def test_everything_else_is_an_address(text: str) -> None:
    parsed = gc.parse_input(text)
    assert parsed.kind == "address" and parsed.text == text.strip()


def test_address_whitespace_is_collapsed() -> None:
    assert gc.parse_input("  401   E Jackson St,\n Tampa ").text == "401 E Jackson St, Tampa"


@given(
    lat=st.floats(-90, 90, allow_nan=False, allow_infinity=False),
    lon=st.floats(-180, 180, allow_nan=False, allow_infinity=False),
)
def test_property_any_in_range_pair_round_trips(lat: float, lon: float) -> None:
    parsed = gc.parse_input(f"{lat:.6f}, {lon:.6f}")
    assert parsed.kind == "coords"
    assert parsed.lat == pytest.approx(lat, abs=1e-6)
    assert parsed.lon == pytest.approx(lon, abs=1e-6)


@given(st.integers(0, 99999))
def test_property_any_five_digit_string_is_a_zip(n: int) -> None:
    text = f"{n:05d}"
    parsed = gc.parse_input(text)
    assert (parsed.kind, parsed.zip) == ("zip", text)


# ---------------------------------------------------------------------------------- locate
class _NeverCalled:
    def geocode(self, query: str) -> GeocodeResult:
        raise AssertionError("the geocoder must not be called for ZIP / coordinate input")


class _Fake:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def geocode(self, query: str) -> GeocodeResult:
        self.queries.append(query)
        cand = GeocodeCandidate(27.9, -82.4, "FAKE ADDRESS", "fake")
        return GeocodeResult(27.9, -82.4, "FAKE ADDRESS", "fake", [cand])


def _zctas():  # type: ignore[no-untyped-def]
    return make_gdf({"33602": box(-3000, -3000, 3000, 3000), "02108": box(8000, 8000, 9000, 9000)})


def test_locate_address_uses_the_geocoder() -> None:
    fake = _Fake()
    res = gc.locate("401 E Jackson St", fake, _zctas())
    assert fake.queries == ["401 E Jackson St"] and res.source == "fake"


def test_locate_zip_uses_the_zcta_representative_point_without_a_geocoder() -> None:
    zctas = _zctas()
    res = gc.locate("33602", _NeverCalled(), zctas)
    row = zctas.set_index("zip").loc["33602"]
    assert (res.lat, res.lon) == (row.intpt_lat, row.intpt_lon)
    assert res.source == "zip"
    assert res.matched_address == "ZIP 33602 (approximate center)"
    assert res.candidates == []


def test_locate_leading_zero_zip() -> None:
    res = gc.locate("02108", _NeverCalled(), _zctas())
    assert res.matched_address == "ZIP 02108 (approximate center)"


def test_locate_zip_plus_four_looks_up_the_zip() -> None:
    assert gc.locate("33602-1234", _NeverCalled(), _zctas()).matched_address.startswith("ZIP 33602")


def test_locate_unknown_zip_says_so_and_names_it() -> None:
    with pytest.raises(gc.AddressNotFoundError, match="99999"):
        gc.locate("99999", _NeverCalled(), _zctas())


def test_locate_coordinates_are_used_directly() -> None:
    res = gc.locate("27.9506, -82.4572", _NeverCalled(), _zctas())
    assert (res.lat, res.lon) == (27.9506, -82.4572)
    assert res.source == "coordinates"
    assert res.matched_address == "27.9506, -82.4572"


def test_locate_invalid_input_propagates() -> None:
    with pytest.raises(gc.InvalidInputError):
        gc.locate("95, 10", _NeverCalled(), _zctas())


def test_locate_geocoder_errors_propagate() -> None:
    class Boom:
        def geocode(self, query: str) -> GeocodeResult:
            raise gc.GeocodeServiceError()

    with pytest.raises(gc.GeocodeServiceError):
        gc.locate("1 Main St", Boom(), _zctas())


def test_locate_zip_without_zcta_data_is_a_clear_error() -> None:
    with pytest.raises(gc.AddressNotFoundError, match="ZIP"):
        gc.locate("33602", _NeverCalled(), None)


def test_locate_works_with_other_regions() -> None:
    zctas = make_gdf({"00901": box(-1000, -1000, 1000, 1000)}, CENTERS["san_juan"])
    res = gc.locate("00901", _NeverCalled(), zctas)
    assert abs(res.lat - CENTERS["san_juan"][0]) < 0.05
    assert (res.lat, res.lon) != TAMPA
