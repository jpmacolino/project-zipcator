# ruff: noqa: RUF001
"""Regression tests for the phase 3 QA review (docs/QA_REPORT.md, phase 3)."""

from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path

import pytest
import requests
import responses
from shapely.geometry import box
from zc_testkit.synth import make_gdf

from zipcator import geocode as gc
from zipcator.models import GeocodeCandidate, GeocodeResult

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "geocode"
CENSUS_URL = gc.CENSUS_URL
NOMINATIM_URL = gc.NOMINATIM_URL


def fixture(name: str) -> object:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


# ------------------------------------------------------------------ rate limiter (>= 3 calls)
def test_limiter_spaces_every_call_not_just_the_second() -> None:
    """Mutant `_last = _last or clock()` throttled only call 2; calls 3+ then burst."""
    ft = FakeTime()
    limiter = gc.RateLimiter(1.0, clock=ft.clock, sleeper=ft.sleep)
    stamps = []
    for _ in range(5):
        limiter.wait()
        stamps.append(ft.now)
    assert ft.sleeps == [1.0, 1.0, 1.0, 1.0]
    assert all(b - a >= 1.0 - 1e-9 for a, b in itertools.pairwise(stamps))


@responses.activate
def test_four_back_to_back_nominatim_requests_are_each_a_second_apart() -> None:
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    ft = FakeTime()
    geo = gc.NominatimGeocoder(
        limiter=gc.RateLimiter(1.0, clock=ft.clock, sleeper=ft.sleep), sleeper=ft.sleep
    )
    for q in "abcd":
        geo.geocode(q)
    assert ft.sleeps == [1.0, 1.0, 1.0]


# ------------------------------------------------------------------------- ASCII-only digits
@pytest.mark.parametrize("text", ["1234", "123456", "12345-678", "33602abc", "ab33602"])
def test_near_miss_zips_are_not_zips(text: str) -> None:
    assert gc.parse_input(text).kind != "zip"


def test_arabic_indic_digits_are_not_a_zip() -> None:
    """`\\d` matches these in Python; the ZIP would be non-ASCII and never match a ZCTA."""
    parsed = gc.parse_input("٣٣٦٠٢")
    assert parsed.kind == "address" and parsed.zip is None


def test_fullwidth_digits_are_normalised_to_a_real_zip() -> None:
    """Typed from a CJK IME: NFKC maps them to ASCII, so this just works."""
    parsed = gc.parse_input("３３６０２")
    assert (parsed.kind, parsed.zip) == ("zip", "33602")
    assert parsed.zip is not None and parsed.zip.isascii()


def test_fullwidth_coordinates_are_normalised_too() -> None:
    parsed = gc.parse_input("２７．９５０６，－８２．４５７２")
    assert parsed.kind == "coords" and parsed.lat == pytest.approx(27.9506)
    assert parsed.lon == pytest.approx(-82.4572)


def test_non_ascii_digit_coordinates_are_an_address_not_silently_numbers() -> None:
    assert gc.parse_input("٣.٥, ١٢").kind == "address"


@pytest.mark.parametrize("text", ["27.9; -82.4", "27.9;-82.4"])
def test_semicolon_separator_is_accepted(text: str) -> None:
    assert gc.parse_input(text).kind == "coords"


@pytest.mark.parametrize("text", ["1e1, 2e1", "NaN, 1", "inf, 1", "1_0, 2_0", "(27.9, -82.4)"])
def test_exotic_number_syntaxes_are_treated_as_addresses(text: str) -> None:
    assert gc.parse_input(text).kind == "address"


def test_negative_zero_is_normalised() -> None:
    parsed = gc.parse_input("-0, -0")
    assert (str(parsed.lat), str(parsed.lon)) == ("0.0", "0.0")


@pytest.mark.parametrize("bad", [None, 123, 3.5, ["a"], b"x"])
def test_non_string_input_is_rejected(bad: object) -> None:
    with pytest.raises(gc.InvalidInputError):
        gc.parse_input(bad)  # type: ignore[arg-type]


def test_overlong_input_is_rejected_with_a_clear_message() -> None:
    """A 3,000-character 'address' made Census return HTML and looked like an outage."""
    with pytest.raises(gc.InvalidInputError, match="too long"):
        gc.parse_input("1 Main St " * 300)
    assert gc.parse_input("x" * gc.MAX_INPUT_CHARS).kind == "address"  # the limit itself is fine


def test_degree_symbol_notation_gets_a_helpful_hint() -> None:
    with pytest.raises(gc.InvalidInputError, match="decimal degrees"):
        gc.parse_input("27.9506° N, 82.4572° W")


def test_compass_letters_in_a_street_address_are_still_an_address() -> None:
    assert gc.parse_input("123 N Main St, Tampa, FL").kind == "address"
    assert gc.parse_input("500 S 5th St").kind == "address"


# ------------------------------------------------------------ "Location is outside the US"
@pytest.mark.parametrize(
    ("name", "lat", "lon"),
    [
        ("Tampa", 27.9506, -82.4572),
        ("Key West", 24.5551, -81.7800),
        ("Maine", 47.3, -68.3),
        ("Seattle", 47.6, -122.3),
        ("San Diego", 32.7, -117.2),
        ("Anchorage", 61.2, -149.9),
        ("Barrow", 71.3, -156.8),
        ("Attu, far-west Aleutians (east longitude)", 52.9, 172.9),
        ("Adak", 51.9, -176.6),
        ("Honolulu", 21.3, -157.9),
        ("Hilo", 19.7, -155.1),
        ("San Juan PR", 18.47, -66.1),
        ("St Thomas VI", 18.34, -64.93),
        ("Guam", 13.44, 144.79),
        ("Saipan", 15.2, 145.75),
        ("Pago Pago", -14.28, -170.7),
        ("just offshore, Gulf of Mexico", 26.0, -85.0),
    ],
)
def test_us_and_territory_coordinates_are_accepted(name: str, lat: float, lon: float) -> None:
    assert gc.in_us_bounds(lat, lon), name
    res = gc.locate(f"{lat}, {lon}", _NoGeocoder(), None)
    assert res.source == "coordinates"


@pytest.mark.parametrize(
    ("name", "lat", "lon"),
    [
        ("Sydney", -33.8688, 151.2093),
        ("London", 51.5074, -0.1278),
        ("Paris", 48.8566, 2.3522),
        ("Mexico City", 19.43, -99.13),
        ("Null Island", 0.0, 0.0),
        ("mid-Atlantic", 30.0, -40.0),
        ("Tokyo", 35.68, 139.69),
    ],
)
def test_non_us_coordinates_raise_outside_the_us(name: str, lat: float, lon: float) -> None:
    assert not gc.in_us_bounds(lat, lon), name
    with pytest.raises(gc.OutsideUSError) as exc:
        gc.locate(f"{lat}, {lon}", _NoGeocoder(), None)
    assert str(exc.value) == "Location is outside the US"


class _NoGeocoder:
    def geocode(self, query: str) -> GeocodeResult:  # pragma: no cover - must not be called
        raise AssertionError("not for coordinates")


class _Fixed:
    def __init__(self, lat: float, lon: float) -> None:
        self.lat, self.lon = lat, lon

    def geocode(self, query: str) -> GeocodeResult:
        c = GeocodeCandidate(self.lat, self.lon, "SOMEWHERE", "fake")
        return GeocodeResult(self.lat, self.lon, "SOMEWHERE", "fake", [c])


def test_a_geocoder_result_outside_the_us_is_rejected() -> None:
    """E.g. a swapped x/y pair or a stray non-US hit: (48.85, 2.35) is Paris."""
    with pytest.raises(gc.OutsideUSError):
        gc.locate("1 Rue de Rivoli", _Fixed(48.8566, 2.3522), None)


def test_swapped_census_coordinates_are_caught_downstream() -> None:
    with pytest.raises(gc.OutsideUSError):
        gc.locate("401 E Jackson St", _Fixed(-82.4567, 27.9472), None)  # lat/lon swapped


def test_a_us_geocoder_result_passes_through() -> None:
    assert gc.locate("401 E Jackson St", _Fixed(27.9472, -82.4567), None).source == "fake"


def test_zip_input_is_never_bounds_checked() -> None:
    zctas = make_gdf({"33602": box(-3000, -3000, 3000, 3000)})
    assert gc.locate("33602", _NoGeocoder(), zctas).source == "zip"


def test_unknown_zip_message_does_not_claim_the_zip_does_not_exist() -> None:
    """00501 is a real USPS ZIP with no ZCTA polygon: say 'no boundary', not 'not a ZIP'."""
    with pytest.raises(gc.AddressNotFoundError) as exc:
        gc.locate("00501", _NoGeocoder(), make_gdf({"33602": box(-1, -1, 1, 1)}))
    assert "00501" in str(exc.value) and "boundary" in str(exc.value)


# ----------------------------------------------------------------------------------- cache
class _Inner:
    def __init__(self) -> None:
        self.calls = 0

    def geocode(self, query: str) -> GeocodeResult:
        self.calls += 1
        c = GeocodeCandidate(1.0, 2.0, query, "x")
        return GeocodeResult(1.0, 2.0, query, "x", [c])


def test_cached_results_cannot_be_poisoned_by_callers() -> None:
    cached = gc.CachedGeocoder(_Inner())
    first = cached.geocode("a")
    first.candidates.append("poison")  # type: ignore[arg-type]
    second = cached.geocode("a")
    assert second.candidates == [GeocodeCandidate(1.0, 2.0, "a", "x")]
    second.candidates.clear()
    assert len(cached.geocode("a").candidates) == 1


@pytest.mark.parametrize("bad", [-1, -100])
def test_negative_cache_size_is_rejected(bad: int) -> None:
    with pytest.raises(ValueError, match="maxsize"):
        gc.CachedGeocoder(_Inner(), maxsize=bad)


def test_zero_cache_size_disables_caching_without_crashing() -> None:
    inner = _Inner()
    cached = gc.CachedGeocoder(inner, maxsize=0)
    cached.geocode("a")
    cached.geocode("a")
    assert inner.calls == 2


@responses.activate
def test_default_geocoder_really_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing CachedGeocoder from default_geocoder() went undetected."""
    monkeypatch.setenv("ZIPCATOR_DISABLE_NOMINATIM", "1")
    responses.get(CENSUS_URL, json=fixture("census_match.json"))
    geo = gc.default_geocoder()
    assert isinstance(geo, gc.CachedGeocoder)
    geo.geocode("401 E Jackson St, Tampa, FL 33602")
    geo.geocode("401 e jackson st tampa fl 33602")
    assert len(responses.calls) == 1


# ------------------------------------------------------------------------- retry details
def _census(ft: FakeTime, **kw: object) -> gc.CensusGeocoder:
    return gc.CensusGeocoder(sleeper=ft.sleep, **kw)  # type: ignore[arg-type]


@responses.activate
def test_negative_retries_still_make_one_request() -> None:
    responses.get(CENSUS_URL, json=fixture("census_match.json"))
    ft = FakeTime()
    assert _census(ft, retries=-1).geocode("x").source == "census"
    assert len(responses.calls) == 1


@responses.activate
def test_http_500_is_treated_as_transient_and_retried() -> None:
    responses.get(CENSUS_URL, status=500)
    responses.get(CENSUS_URL, json=fixture("census_match.json"))
    assert _census(FakeTime()).geocode("x").source == "census"
    assert len(responses.calls) == 2


@responses.activate
def test_client_error_with_a_json_body_is_still_an_error_not_a_result() -> None:
    responses.get(CENSUS_URL, status=403, json=fixture("census_match.json"))
    with pytest.raises(gc.GeocodeServiceError):
        _census(FakeTime()).geocode("x")
    assert len(responses.calls) == 1


@responses.activate
def test_retry_after_header_is_honoured_when_reasonable() -> None:
    responses.get(NOMINATIM_URL, status=429, headers={"Retry-After": "7"})
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    ft = FakeTime()
    geo = gc.NominatimGeocoder(
        limiter=gc.RateLimiter(0.0, clock=ft.clock, sleeper=ft.sleep),
        sleeper=ft.sleep,
        backoff=1.0,
    )
    assert geo.geocode("x").source == "nominatim"
    assert 7.0 in ft.sleeps  # waited what the server asked, not the 1 s backoff


@responses.activate
def test_unreasonable_retry_after_gives_up_instead_of_hammering() -> None:
    responses.get(NOMINATIM_URL, status=429, headers={"Retry-After": "3600"})
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    ft = FakeTime()
    geo = gc.NominatimGeocoder(
        limiter=gc.RateLimiter(0.0, clock=ft.clock, sleeper=ft.sleep), sleeper=ft.sleep
    )
    with pytest.raises(gc.GeocodeServiceError):
        geo.geocode("x")
    assert len(responses.calls) == 1 and max(ft.sleeps, default=0) < 3600


@responses.activate
def test_unparseable_retry_after_falls_back_to_the_normal_backoff() -> None:
    responses.get(
        NOMINATIM_URL, status=429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    )
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    ft = FakeTime()
    geo = gc.NominatimGeocoder(
        limiter=gc.RateLimiter(0.0, clock=ft.clock, sleeper=ft.sleep),
        sleeper=ft.sleep,
        backoff=2.0,
    )
    assert geo.geocode("x").source == "nominatim"
    assert 2.0 in ft.sleeps


def test_invalid_user_agent_is_not_retried_and_is_logged(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZIPCATOR_USER_AGENT", "bad\r\nX-Injected: 1")
    ft = FakeTime()
    with caplog.at_level("ERROR", logger="zipcator.geocode"), pytest.raises(gc.GeocodeServiceError):
        _census(ft).geocode("x")
    assert "ZIPCATOR_USER_AGENT" in caplog.text and ft.sleeps == []


# ----------------------------------------------------- survivors from the mutation run
@responses.activate
def test_uppercase_country_code_is_accepted() -> None:
    body = copy.deepcopy(fixture("nominatim_match.json"))
    body[0]["address"]["country_code"] = "US"  # type: ignore[index]
    responses.get(NOMINATIM_URL, json=body)
    ft = FakeTime()
    geo = gc.NominatimGeocoder(limiter=gc.RateLimiter(0.0), sleeper=ft.sleep)
    assert geo.geocode("x").source == "nominatim"


def test_chain_does_not_fall_back_on_invalid_input() -> None:
    class Invalid:
        def geocode(self, query: str) -> GeocodeResult:
            raise gc.InvalidInputError("nope")

    class Fallback:
        def geocode(self, query: str) -> GeocodeResult:  # pragma: no cover
            raise AssertionError("fallback must not be consulted for invalid input")

    with pytest.raises(gc.InvalidInputError):
        gc.ChainGeocoder(Invalid(), Fallback()).geocode("x")


@responses.activate
def test_nominatim_sends_the_configured_result_limit_and_keeps_candidate_order() -> None:
    base = copy.deepcopy(fixture("nominatim_match.json"))[0]  # type: ignore[index]
    second = copy.deepcopy(base)
    second["display_name"] = "Second Place, USA"
    responses.get(NOMINATIM_URL, json=[base, second])
    geo = gc.NominatimGeocoder(limiter=gc.RateLimiter(0.0), limit=2)
    res = geo.geocode("x")
    assert "limit=2" in responses.calls[0].request.url
    assert [c.matched_address for c in res.candidates] == [
        base["display_name"],
        "Second Place, USA",
    ]


@responses.activate
def test_census_candidates_keep_the_services_best_first_order() -> None:
    body = fixture("census_multi.json")
    responses.get(CENSUS_URL, json=body)
    res = gc.CensusGeocoder().geocode("100 Main St, Springfield")
    expected = [m["matchedAddress"] for m in body["result"]["addressMatches"]]  # type: ignore[index]
    assert [c.matched_address for c in res.candidates] == expected


@responses.activate
def test_explicit_user_agent_argument_overrides_the_default() -> None:
    responses.get(CENSUS_URL, json=fixture("census_match.json"))
    gc.CensusGeocoder(user_agent="my-tool/9").geocode("x")
    assert responses.calls[0].request.headers["User-Agent"] == "my-tool/9"


@responses.activate
def test_user_agent_env_change_is_picked_up_at_request_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses.get(CENSUS_URL, json=fixture("census_match.json"))
    geo = gc.CensusGeocoder()  # constructed before the env var is set
    monkeypatch.setenv("ZIPCATOR_USER_AGENT", "late-binding/1")
    geo.geocode("x")
    assert responses.calls[0].request.headers["User-Agent"] == "late-binding/1"


@responses.activate
def test_special_characters_are_url_encoded_in_the_query() -> None:
    responses.get(CENSUS_URL, json=fixture("census_match.json"))
    gc.CensusGeocoder().geocode("7 O'Brien & Sons #5, Doña Ana, NM")
    url = responses.calls[0].request.url
    assert "%26" in url and "%23" in url and "%C3%B1" in url  # & # ñ encoded
    assert "&Sons" not in url and "#5" not in url


def test_connection_failure_message_never_reaches_the_user() -> None:
    """The user-facing text is the fixed spec message, not a requests exception string."""
    with responses.RequestsMock() as rsps:
        rsps.get(CENSUS_URL, body=requests.ConnectionError("HTTPSConnectionPool(host=secret)"))
        rsps.get(CENSUS_URL, body=requests.ConnectionError("HTTPSConnectionPool(host=secret)"))
        with pytest.raises(gc.GeocodeServiceError) as exc:
            _census(FakeTime()).geocode("x")
    assert "secret" not in str(exc.value)
