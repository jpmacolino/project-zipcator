"""Census / Nominatim geocoders, the fallback chain, caching and rate limiting (SPEC §5.2).

HTTP is mocked with ``responses`` using recorded fixtures (tests/fixtures/geocode). Time is
injected through a fake clock/sleeper, so nothing here sleeps or touches the network.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import requests
import responses
from responses import matchers

import zipcator
from zipcator import config
from zipcator import geocode as gc
from zipcator.models import GeocodeCandidate, GeocodeResult

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "geocode"
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ADDRESS = "401 E Jackson St, Tampa, FL 33602"
SERVICE_DOWN = "Geocoding service unavailable — try again or enter coordinates"


def fixture(name: str) -> object:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeTime:
    """Injectable clock + sleeper: sleeping advances the clock instantly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def ft() -> FakeTime:
    return FakeTime()


def census(ft: FakeTime, **kw: object) -> gc.CensusGeocoder:
    return gc.CensusGeocoder(sleeper=ft.sleep, **kw)  # type: ignore[arg-type]


def nominatim(ft: FakeTime, **kw: object) -> gc.NominatimGeocoder:
    limiter = gc.RateLimiter(1.0, clock=ft.clock, sleeper=ft.sleep)
    return gc.NominatimGeocoder(sleeper=ft.sleep, limiter=limiter, **kw)  # type: ignore[arg-type]


# ================================================================================== Census
@responses.activate
def test_census_match_returns_typed_result(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, json=fixture("census_match.json"))
    res = census(ft).geocode(ADDRESS)
    assert isinstance(res, GeocodeResult)
    assert res.lat == pytest.approx(27.947190029308)  # y is latitude
    assert res.lon == pytest.approx(-82.456673751437)  # x is longitude
    assert res.matched_address == "401 JACKSON ST, TAMPA, FL, 33602"
    assert res.source == "census"
    assert res.candidates == [GeocodeCandidate(res.lat, res.lon, res.matched_address, "census")]


@responses.activate
def test_census_request_uses_the_documented_endpoint_params_timeout_and_user_agent(
    ft: FakeTime,
) -> None:
    responses.get(
        CENSUS_URL,
        json=fixture("census_match.json"),
        match=[
            matchers.query_param_matcher(
                {"address": ADDRESS, "benchmark": "Public_AR_Current", "format": "json"}
            )
        ],
    )
    census(ft).geocode(ADDRESS)
    (call,) = responses.calls
    assert call.request.req_kwargs["timeout"] == 10  # spec: 10 s timeout
    assert call.request.headers["User-Agent"].startswith("zipcator/")


@responses.activate
def test_census_multiple_candidates_best_first(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, json=fixture("census_multi.json"))
    res = census(ft).geocode("100 Main St, Springfield")
    assert len(res.candidates) >= 2
    assert (res.lat, res.lon, res.matched_address) == (
        res.candidates[0].lat,
        res.candidates[0].lon,
        res.candidates[0].matched_address,
    )
    states = {c.matched_address.split(", ")[-2] for c in res.candidates}
    assert len(states) >= 2  # genuinely ambiguous: different states
    assert all(c.source == "census" for c in res.candidates)


@responses.activate
def test_census_no_match_raises_address_not_found(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, json=fixture("census_nomatch.json"))
    with pytest.raises(gc.AddressNotFoundError) as exc:
        census(ft).geocode("zzzz qqqq nowhere 99999")
    assert str(exc.value) == "Address not found"


@responses.activate
def test_census_skips_malformed_matches_but_keeps_good_ones(ft: FakeTime) -> None:
    body = copy.deepcopy(fixture("census_multi.json"))
    matches = body["result"]["addressMatches"]  # type: ignore[index]
    matches[0]["coordinates"] = {"x": "oops"}
    matches[1].pop("coordinates")
    matches.append({"matchedAddress": "BAD", "coordinates": {"x": 999, "y": 999}})
    responses.get(CENSUS_URL, json=body)
    res = census(ft).geocode("x")
    assert all(-180 <= c.lon <= 180 and -90 <= c.lat <= 90 for c in res.candidates)
    assert "BAD" not in [c.matched_address for c in res.candidates]
    assert len(res.candidates) == len(matches) - 3


@responses.activate
def test_census_only_malformed_matches_means_not_found(ft: FakeTime) -> None:
    responses.get(
        CENSUS_URL,
        json={"result": {"addressMatches": [{"matchedAddress": "X", "coordinates": {}}]}},
    )
    with pytest.raises(gc.AddressNotFoundError):
        census(ft).geocode("x")


@responses.activate
def test_census_timeout_then_success_retries_once_with_backoff(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, body=requests.Timeout("slow"))
    responses.get(CENSUS_URL, json=fixture("census_match.json"))
    res = census(ft, backoff=1.0).geocode(ADDRESS)
    assert res.source == "census"
    assert len(responses.calls) == 2
    assert ft.sleeps == [1.0]  # injected sleeper: no real sleeping


@responses.activate
def test_census_server_error_then_success(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, status=503)
    responses.get(CENSUS_URL, json=fixture("census_match.json"))
    assert census(ft).geocode(ADDRESS).source == "census"
    assert len(responses.calls) == 2


@responses.activate
def test_census_persistent_failure_raises_service_error_after_exactly_one_retry(
    ft: FakeTime,
) -> None:
    responses.get(CENSUS_URL, body=requests.Timeout("slow"))
    responses.get(CENSUS_URL, status=503)
    with pytest.raises(gc.GeocodeServiceError) as exc:
        census(ft).geocode(ADDRESS)
    assert str(exc.value) == SERVICE_DOWN
    assert len(responses.calls) == 2  # original + one retry, no more


@responses.activate
def test_census_client_error_is_not_retried(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, status=400, body="bad request")
    with pytest.raises(gc.GeocodeServiceError):
        census(ft).geocode(ADDRESS)
    assert len(responses.calls) == 1 and ft.sleeps == []


@responses.activate
def test_census_connection_error_is_a_service_error(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, body=requests.ConnectionError("dns"))
    responses.get(CENSUS_URL, body=requests.ConnectionError("dns"))
    with pytest.raises(gc.GeocodeServiceError):
        census(ft).geocode(ADDRESS)


@responses.activate
def test_census_garbage_body_is_a_service_error(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, body="<html>maintenance</html>")
    with pytest.raises(gc.GeocodeServiceError):
        census(ft, retries=0).geocode(ADDRESS)


@responses.activate
def test_census_unexpected_json_shape_is_a_service_error(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, json={"errors": ["Internal Server Error"]})
    with pytest.raises(gc.GeocodeServiceError):
        census(ft, retries=0).geocode(ADDRESS)


def test_empty_query_is_rejected_before_any_request(ft: FakeTime) -> None:
    with responses.RequestsMock() as rsps:
        with pytest.raises(gc.InvalidInputError):
            census(ft).geocode("   ")
        assert len(rsps.calls) == 0


# ================================================================================ Nominatim
@responses.activate
def test_nominatim_match_parses_string_coordinates(ft: FakeTime) -> None:
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    res = nominatim(ft).geocode("Tampa Theatre, Franklin St, Tampa")
    assert (res.lat, res.lon) == (pytest.approx(27.9503814), pytest.approx(-82.4588074))
    assert res.source == "nominatim"
    assert res.matched_address.startswith("Tampa Theatre, 711, North Franklin Street")
    assert len(res.candidates) == 1


@responses.activate
def test_nominatim_request_policy_params_and_user_agent(
    ft: FakeTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Usage policy: countrycodes=us, descriptive User-Agent, identifiable app."""
    monkeypatch.delenv("ZIPCATOR_USER_AGENT", raising=False)
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    nominatim(ft).geocode("Tampa Theatre")
    (call,) = responses.calls
    url = call.request.url
    assert "countrycodes=us" in url and "format=jsonv2" in url and "addressdetails=1" in url
    assert call.request.headers["User-Agent"] == (
        f"zipcator/{zipcator.__version__} (+{config.REPO_URL})"
    )
    assert call.request.req_kwargs["timeout"] == 10


@responses.activate
def test_nominatim_user_agent_is_configurable_by_env(
    ft: FakeTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZIPCATOR_USER_AGENT", "acme-mailer/2.0 (ops@acme.example)")
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    nominatim(ft).geocode("Tampa Theatre")
    assert responses.calls[0].request.headers["User-Agent"] == "acme-mailer/2.0 (ops@acme.example)"


@responses.activate
def test_nominatim_rejects_non_us_results(ft: FakeTime) -> None:
    body = copy.deepcopy(fixture("nominatim_match.json"))
    body[0]["address"]["country_code"] = "ca"  # type: ignore[index]
    responses.get(NOMINATIM_URL, json=body)
    with pytest.raises(gc.OutsideUSError) as exc:
        nominatim(ft).geocode("somewhere in Ontario")
    assert str(exc.value) == "Location is outside the US"


@responses.activate
def test_nominatim_keeps_only_the_us_candidates_of_a_mixed_list(ft: FakeTime) -> None:
    body = copy.deepcopy(fixture("nominatim_match.json"))
    foreign = copy.deepcopy(body[0])  # type: ignore[index]
    foreign["address"]["country_code"] = "mx"
    foreign["display_name"] = "Foreign Place"
    responses.get(NOMINATIM_URL, json=[foreign, *body])  # type: ignore[misc]
    res = nominatim(ft).geocode("Tampa Theatre")
    assert [c.matched_address for c in res.candidates] == [body[0]["display_name"]]  # type: ignore[index]


@responses.activate
def test_nominatim_result_without_country_code_is_not_trusted(ft: FakeTime) -> None:
    body = copy.deepcopy(fixture("nominatim_match.json"))
    body[0]["address"].pop("country_code")  # type: ignore[index]
    responses.get(NOMINATIM_URL, json=body)
    with pytest.raises(gc.OutsideUSError):
        nominatim(ft).geocode("Tampa Theatre")


@responses.activate
def test_nominatim_empty_list_is_not_found(ft: FakeTime) -> None:
    responses.get(NOMINATIM_URL, json=[])
    with pytest.raises(gc.AddressNotFoundError):
        nominatim(ft).geocode("nowhere")


@responses.activate
def test_nominatim_retries_429_once_then_gives_up(ft: FakeTime) -> None:
    responses.get(NOMINATIM_URL, status=429)
    responses.get(NOMINATIM_URL, status=429)
    with pytest.raises(gc.GeocodeServiceError) as exc:
        nominatim(ft).geocode("Tampa Theatre")
    assert str(exc.value) == SERVICE_DOWN and len(responses.calls) == 2


@responses.activate
def test_nominatim_garbage_is_a_service_error(ft: FakeTime) -> None:
    responses.get(NOMINATIM_URL, body="not json")
    with pytest.raises(gc.GeocodeServiceError):
        nominatim(ft, retries=0).geocode("Tampa Theatre")


# ---------------------------------------------------------------------------- rate limiting
@responses.activate
def test_nominatim_first_request_is_not_delayed_and_the_second_waits_a_full_second(
    ft: FakeTime,
) -> None:
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    geo = nominatim(ft)
    geo.geocode("a")
    assert ft.sleeps == []
    geo.geocode("b")  # immediately after: must wait the whole interval
    assert ft.sleeps == [pytest.approx(1.0)]


@responses.activate
def test_nominatim_rate_limit_waits_only_for_the_remainder(ft: FakeTime) -> None:
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    geo = nominatim(ft)
    geo.geocode("a")
    ft.now += 0.4  # 0.4 s pass on their own
    geo.geocode("b")
    assert ft.sleeps == [pytest.approx(0.6)]


@responses.activate
def test_nominatim_no_wait_when_a_second_has_already_passed(ft: FakeTime) -> None:
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    geo = nominatim(ft)
    geo.geocode("a")
    ft.now += 5
    geo.geocode("b")
    assert ft.sleeps == []


@responses.activate
def test_rate_limiter_is_shared_so_two_instances_still_obey_one_request_per_second(
    ft: FakeTime,
) -> None:
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    limiter = gc.RateLimiter(1.0, clock=ft.clock, sleeper=ft.sleep)
    a = gc.NominatimGeocoder(limiter=limiter, sleeper=ft.sleep)
    b = gc.NominatimGeocoder(limiter=limiter, sleeper=ft.sleep)
    a.geocode("x")
    b.geocode("y")
    assert ft.sleeps == [pytest.approx(1.0)]


@responses.activate
def test_retry_requests_also_respect_the_rate_limit(ft: FakeTime) -> None:
    """The retry after a 503 must not slip under the 1 req/s ceiling."""
    responses.get(NOMINATIM_URL, status=503)
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    nominatim(ft, backoff=0.0).geocode("Tampa Theatre")
    stamps = [s for s in ft.sleeps if s > 0]
    assert sum(stamps) >= 1.0 - 1e-9  # the retry waited for the limiter


def test_default_shared_limiter_is_one_second() -> None:
    assert gc.NominatimGeocoder().limiter.min_interval == 1.0


# =================================================================================== Chain
def _cen_ok() -> None:
    responses.get(CENSUS_URL, json=fixture("census_match.json"))


@responses.activate
def test_chain_uses_census_and_never_touches_nominatim_on_success(ft: FakeTime) -> None:
    _cen_ok()
    chain = gc.ChainGeocoder(census(ft), nominatim(ft))
    assert chain.geocode(ADDRESS).source == "census"
    assert [c.request.url.split("?")[0] for c in responses.calls] == [CENSUS_URL]


@responses.activate
def test_chain_falls_back_to_nominatim_when_census_has_no_match(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, json=fixture("census_nomatch.json"))
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    res = gc.ChainGeocoder(census(ft), nominatim(ft)).geocode("Tampa Theatre")
    assert res.source == "nominatim"


@responses.activate
def test_chain_falls_back_when_census_errors(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, status=503)
    responses.get(CENSUS_URL, status=503)
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    assert gc.ChainGeocoder(census(ft), nominatim(ft)).geocode("x").source == "nominatim"


@responses.activate
def test_chain_both_find_nothing_is_address_not_found(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, json=fixture("census_nomatch.json"))
    responses.get(NOMINATIM_URL, json=[])
    with pytest.raises(gc.AddressNotFoundError):
        gc.ChainGeocoder(census(ft), nominatim(ft)).geocode("x")


@responses.activate
def test_chain_both_services_down_is_a_service_error(ft: FakeTime) -> None:
    for url in (CENSUS_URL, NOMINATIM_URL):
        responses.get(url, status=503)
        responses.get(url, status=503)
    with pytest.raises(gc.GeocodeServiceError) as exc:
        gc.ChainGeocoder(census(ft), nominatim(ft)).geocode("x")
    assert str(exc.value) == SERVICE_DOWN


@responses.activate
def test_chain_census_no_match_and_nominatim_down_is_a_service_error(ft: FakeTime) -> None:
    """We cannot claim 'not found' when the second opinion was unreachable."""
    responses.get(CENSUS_URL, json=fixture("census_nomatch.json"))
    responses.get(NOMINATIM_URL, status=503)
    responses.get(NOMINATIM_URL, status=503)
    with pytest.raises(gc.GeocodeServiceError):
        gc.ChainGeocoder(census(ft), nominatim(ft)).geocode("x")


@responses.activate
def test_chain_nominatim_outside_us_error_propagates(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, json=fixture("census_nomatch.json"))
    body = copy.deepcopy(fixture("nominatim_match.json"))
    body[0]["address"]["country_code"] = "gb"  # type: ignore[index]
    responses.get(NOMINATIM_URL, json=body)
    with pytest.raises(gc.OutsideUSError):
        gc.ChainGeocoder(census(ft), nominatim(ft)).geocode("10 Downing St")


@responses.activate
def test_chain_without_fallback_raises_the_census_error(ft: FakeTime) -> None:
    responses.get(CENSUS_URL, json=fixture("census_nomatch.json"))
    with pytest.raises(gc.AddressNotFoundError):
        gc.ChainGeocoder(census(ft), None).geocode("x")
    assert len(responses.calls) == 1


# -------------------------------------------------------------------- default_geocoder / env
@responses.activate
def test_nominatim_can_be_disabled_by_env_var(
    ft: FakeTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZIPCATOR_DISABLE_NOMINATIM", "1")
    responses.get(CENSUS_URL, json=fixture("census_nomatch.json"))
    geo = gc.default_geocoder()
    with pytest.raises(gc.AddressNotFoundError):
        geo.geocode("Tampa Theatre")
    assert [c.request.url.split("?")[0] for c in responses.calls] == [CENSUS_URL]  # no fallback


@responses.activate
def test_nominatim_fallback_is_active_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZIPCATOR_DISABLE_NOMINATIM", raising=False)
    monkeypatch.setattr(gc, "_SHARED_LIMITER", gc.RateLimiter(0.0))  # keep the test instant
    responses.get(CENSUS_URL, json=fixture("census_nomatch.json"))
    responses.get(NOMINATIM_URL, json=fixture("nominatim_match.json"))
    assert gc.default_geocoder().geocode("Tampa Theatre").source == "nominatim"


# ================================================================================== Cache
class _Counting:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def geocode(self, query: str) -> GeocodeResult:
        self.calls.append(query)
        c = GeocodeCandidate(1.0, 2.0, query, "x")
        return GeocodeResult(1.0, 2.0, query, "x", [c])


def test_cache_hits_for_normalised_equivalent_queries() -> None:
    inner = _Counting()
    cached = gc.CachedGeocoder(inner)
    first = cached.geocode("401 E Jackson St, Tampa, FL 33602")
    again = cached.geocode("  401  e jackson st   TAMPA fl 33602 ")  # case, spacing, comma
    assert inner.calls == ["401 E Jackson St, Tampa, FL 33602"]
    assert again is first


def test_cache_distinguishes_different_addresses() -> None:
    inner = _Counting()
    cached = gc.CachedGeocoder(inner)
    cached.geocode("1 Main St")
    cached.geocode("2 Main St")
    assert len(inner.calls) == 2


def test_cache_does_not_store_failures() -> None:
    class Flaky:
        n = 0

        def geocode(self, query: str) -> GeocodeResult:
            Flaky.n += 1
            if Flaky.n == 1:
                raise gc.GeocodeServiceError()
            return GeocodeResult(1.0, 2.0, query, "x", [])

    cached = gc.CachedGeocoder(Flaky())
    with pytest.raises(gc.GeocodeServiceError):
        cached.geocode("a")
    assert cached.geocode("a").lat == 1.0  # the transient failure was not remembered


def test_cache_is_bounded_lru() -> None:
    inner = _Counting()
    cached = gc.CachedGeocoder(inner, maxsize=2)
    for q in ("a", "b"):
        cached.geocode(q)
    cached.geocode("a")  # refresh a -> b is now least recently used
    cached.geocode("c")  # evicts b
    inner.calls.clear()
    cached.geocode("a")
    cached.geocode("b")
    assert inner.calls == ["b"]


def test_normalize_key() -> None:
    assert gc.normalize_query("  401 E.  Jackson St,\n Tampa ") == gc.normalize_query(
        "401 e. jackson st tampa"
    )


# ============================================================================ the protocol
def test_fakes_satisfy_the_geocoder_protocol_so_tests_can_inject_them() -> None:
    assert isinstance(_Counting(), gc.Geocoder)
    assert isinstance(gc.CensusGeocoder(), gc.Geocoder)
    assert isinstance(gc.NominatimGeocoder(), gc.Geocoder)
    assert isinstance(gc.ChainGeocoder(gc.CensusGeocoder(), None), gc.Geocoder)
    assert isinstance(gc.CachedGeocoder(_Counting()), gc.Geocoder)


def test_error_hierarchy_and_exact_user_messages() -> None:
    for exc in (gc.AddressNotFoundError, gc.GeocodeServiceError, gc.OutsideUSError):
        assert issubclass(exc, gc.GeocodeError)
    assert str(gc.AddressNotFoundError()) == "Address not found"
    assert str(gc.GeocodeServiceError()) == SERVICE_DOWN
    assert str(gc.OutsideUSError()) == "Location is outside the US"
