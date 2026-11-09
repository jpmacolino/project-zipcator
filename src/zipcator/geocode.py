"""Geocoding and user-input parsing (docs/SPEC.md §5.1, §5.2).

Census Geocoder is primary; Nominatim is a polite fallback (usage policy: ``countrycodes=us``,
descriptive ``User-Agent``, at most 1 request/second, results cached). Everything is behind the
:class:`Geocoder` protocol so tests inject fakes, and time (``clock``/``sleeper``) is injected
so tests never sleep. The library never prints; it logs and raises typed errors whose messages
are written for end users.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

import geopandas as gpd
import requests

from zipcator import config
from zipcator.models import GeocodeCandidate, GeocodeResult

logger = logging.getLogger(__name__)

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
CENSUS_BENCHMARK = "Public_AR_Current"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
TIMEOUT_S = 10.0
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_ENTER_HINT = "Enter an address, a 5-digit ZIP code, or coordinates like 27.9506, -82.4572"


# ------------------------------------------------------------------------------------ errors
class GeocodeError(Exception):
    """Base class; every message is suitable to show to the user."""


class InvalidInputError(GeocodeError, ValueError):
    """The text is empty or is coordinates outside the valid range."""


class AddressNotFoundError(GeocodeError):
    def __init__(self, message: str = "Address not found") -> None:
        super().__init__(message)


class GeocodeServiceError(GeocodeError):
    def __init__(
        self, message: str = "Geocoding service unavailable — try again or enter coordinates"
    ) -> None:
        super().__init__(message)


class OutsideUSError(GeocodeError):
    def __init__(self, message: str = "Location is outside the US") -> None:
        super().__init__(message)


@runtime_checkable
class Geocoder(Protocol):
    """Anything that turns a one-line address into a :class:`GeocodeResult`."""

    def geocode(self, query: str) -> GeocodeResult: ...


# ---------------------------------------------------------------------------- input parsing
@dataclass(frozen=True)
class ParsedInput:
    """What the user typed, classified (SPEC §5.1)."""

    kind: Literal["zip", "coords", "address"]
    text: str
    lat: float | None = None
    lon: float | None = None
    zip: str | None = None


_ZIP_RE = re.compile(r"^(\d{5})(?:-\d{4})?$")
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_COORD_RE = re.compile(rf"^({_NUM})\s*(?:,|;|\s)\s*({_NUM})$")


def parse_input(text: str) -> ParsedInput:
    """Classify free text as a bare ZIP, a ``lat, lon`` pair, or an address.

    Two numbers and nothing else are *always* coordinates, so an out-of-range pair such as
    ``"95, 10"`` is an error rather than a nonsensical address lookup.
    """
    clean = " ".join(str(text).split())
    if not clean:
        raise InvalidInputError(_ENTER_HINT)
    if m := _ZIP_RE.match(clean):
        return ParsedInput("zip", clean, zip=m.group(1))
    if m := _COORD_RE.match(clean):
        lat, lon = float(m.group(1)), float(m.group(2))
        if not -90 <= lat <= 90:
            raise InvalidInputError(
                f"Invalid coordinates: latitude must be between -90 and 90 (got {lat:g})"
            )
        if not -180 <= lon <= 180:
            raise InvalidInputError(
                f"Invalid coordinates: longitude must be between -180 and 180 (got {lon:g})"
            )
        return ParsedInput("coords", clean, lat=lat, lon=lon)
    return ParsedInput("address", clean)


def normalize_query(query: str) -> str:
    """Cache key: case-insensitive, comma- and whitespace-insensitive."""
    return " ".join(query.casefold().replace(",", " ").split())


# ------------------------------------------------------------------------------ rate limit
class RateLimiter:
    """Blocks so successive ``wait()`` calls are at least ``min_interval`` seconds apart.

    Thread-safe (Streamlit serves sessions from several threads); ``clock``/``sleeper`` are
    injectable so tests use a fake clock and never really sleep.
    """

    def __init__(
        self,
        min_interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleeper = sleeper
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            if self._last is not None:
                remaining = self.min_interval - (self._clock() - self._last)
                if remaining > 0:
                    self._sleeper(remaining)
            self._last = self._clock()


#: Shared by every Nominatim client in the process: the policy limit is per client, not per
#: object, so two instances must not each send one request per second.
_SHARED_LIMITER = RateLimiter(1.0)


# ------------------------------------------------------------------------------ HTTP core
def _get_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    retries: int,
    backoff: float,
    sleeper: Callable[[float], None],
    before_request: Callable[[], None] | None = None,
) -> Any:
    """GET + parse JSON with one retry (exponential backoff) for transient failures."""
    for attempt in range(retries + 1):
        if attempt:
            sleeper(backoff * 2 ** (attempt - 1))
        if before_request is not None:
            before_request()
        try:
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:  # timeouts, DNS, resets: transient
            logger.warning(
                "geocoder request failed (attempt %d): %s", attempt + 1, type(exc).__name__
            )
            continue
        if resp.status_code in _TRANSIENT_STATUS:
            logger.warning("geocoder returned HTTP %d (attempt %d)", resp.status_code, attempt + 1)
            continue
        if resp.status_code != 200:
            logger.warning("geocoder returned HTTP %d; not retrying", resp.status_code)
            raise GeocodeServiceError()
        try:
            return resp.json()
        except ValueError as exc:
            logger.warning("geocoder returned a non-JSON body")
            raise GeocodeServiceError() from exc
    raise GeocodeServiceError()


def _finite_coord(lat: object, lon: object) -> tuple[float, float] | None:
    try:
        la, lo = float(lat), float(lon)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(la) and math.isfinite(lo) and -90 <= la <= 90 and -180 <= lo <= 180):
        return None
    return la, lo


def _result(candidates: list[GeocodeCandidate]) -> GeocodeResult:
    best = candidates[0]
    return GeocodeResult(best.lat, best.lon, best.matched_address, best.source, candidates)


class CensusGeocoder:
    """US Census Geocoder, one-line address endpoint (primary; no key needed)."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = TIMEOUT_S,
        retries: int = 1,
        backoff: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
        user_agent: str | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.sleeper = sleeper
        self.user_agent = user_agent

    def geocode(self, query: str) -> GeocodeResult:
        text = " ".join(query.split())
        if not text:
            raise InvalidInputError(_ENTER_HINT)
        data = _get_json(
            self.session,
            CENSUS_URL,
            params={"address": text, "benchmark": CENSUS_BENCHMARK, "format": "json"},
            headers={"User-Agent": self.user_agent or config.user_agent()},
            timeout=self.timeout,
            retries=self.retries,
            backoff=self.backoff,
            sleeper=self.sleeper,
        )
        try:
            matches = data["result"]["addressMatches"]
            iter(matches)
        except (KeyError, TypeError) as exc:
            logger.warning("unexpected Census geocoder response shape")
            raise GeocodeServiceError() from exc
        candidates: list[GeocodeCandidate] = []
        for match in matches:
            try:
                coords = match["coordinates"]
                point = _finite_coord(coords["y"], coords["x"])  # x = longitude, y = latitude
                address = str(match.get("matchedAddress") or text)
            except (KeyError, TypeError, AttributeError):
                continue
            if point is not None:
                candidates.append(GeocodeCandidate(point[0], point[1], address, "census"))
        if not candidates:
            raise AddressNotFoundError()
        return _result(candidates)


class NominatimGeocoder:
    """OpenStreetMap Nominatim (fallback), used within its usage policy.

    ``countrycodes=us``, a descriptive configurable ``User-Agent`` (``ZIPCATOR_USER_AGENT``),
    at most one request per second (including retries), and results are expected to be
    cached by wrapping this in :class:`CachedGeocoder`.
    """

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = TIMEOUT_S,
        retries: int = 1,
        backoff: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
        user_agent: str | None = None,
        limiter: RateLimiter | None = None,
        limit: int = 5,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.sleeper = sleeper
        self.user_agent = user_agent
        self.limiter = limiter or _SHARED_LIMITER
        self.limit = limit

    def geocode(self, query: str) -> GeocodeResult:
        text = " ".join(query.split())
        if not text:
            raise InvalidInputError(_ENTER_HINT)
        data = _get_json(
            self.session,
            NOMINATIM_URL,
            params={
                "q": text,
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": self.limit,
                "countrycodes": "us",
            },
            headers={"User-Agent": self.user_agent or config.user_agent()},
            timeout=self.timeout,
            retries=self.retries,
            backoff=self.backoff,
            sleeper=self.sleeper,
            before_request=self.limiter.wait,
        )
        if not isinstance(data, list):
            logger.warning("unexpected Nominatim response shape")
            raise GeocodeServiceError()
        if not data:
            raise AddressNotFoundError()
        candidates: list[GeocodeCandidate] = []
        saw_foreign = False
        for item in data:
            if not isinstance(item, dict):
                continue
            country = str((item.get("address") or {}).get("country_code", "")).lower()
            if country != "us":
                saw_foreign = True
                continue
            point = _finite_coord(item.get("lat"), item.get("lon"))
            if point is not None:
                name = str(item.get("display_name") or text)
                candidates.append(GeocodeCandidate(point[0], point[1], name, "nominatim"))
        if candidates:
            return _result(candidates)
        raise OutsideUSError() if saw_foreign else AddressNotFoundError()


class ChainGeocoder:
    """Try ``primary``; if it finds nothing or fails, ask ``fallback`` (if any).

    When both fail, the *fallback's* error is raised: if the second opinion was unreachable we
    cannot honestly say "not found", and an authoritative answer from it should win.
    """

    def __init__(self, primary: Geocoder, fallback: Geocoder | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    def geocode(self, query: str) -> GeocodeResult:
        try:
            return self.primary.geocode(query)
        except (AddressNotFoundError, GeocodeServiceError):
            if self.fallback is None:
                raise
            logger.info("primary geocoder had no usable answer; trying the fallback")
        return self.fallback.geocode(query)


class CachedGeocoder:
    """Bounded in-process LRU cache keyed by the normalised query; failures are not cached."""

    def __init__(self, inner: Geocoder, maxsize: int = 256) -> None:
        self.inner = inner
        self.maxsize = maxsize
        self._cache: OrderedDict[str, GeocodeResult] = OrderedDict()
        self._lock = threading.Lock()

    def geocode(self, query: str) -> GeocodeResult:
        key = normalize_query(query)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        result = self.inner.geocode(query)
        with self._lock:
            self._cache[key] = result
            self._cache.move_to_end(key)
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)
        return result


def default_geocoder(session: requests.Session | None = None) -> Geocoder:
    """Census → Nominatim (unless ``ZIPCATOR_DISABLE_NOMINATIM`` is set), cached."""
    fallback = None if config.nominatim_disabled() else NominatimGeocoder(session=session)
    return CachedGeocoder(ChainGeocoder(CensusGeocoder(session=session), fallback))


# --------------------------------------------------------------------------------- locate
def locate(text: str, geocoder: Geocoder, zctas: gpd.GeoDataFrame | None) -> GeocodeResult:
    """Resolve what the user typed to a search center (SPEC §5.1).

    * ``lat, lon`` → used directly (no geocoder);
    * a bare ZIP → that ZCTA's representative point, labelled as approximate (no geocoder);
    * anything else → the geocoder.
    """
    parsed = parse_input(text)
    if parsed.kind == "coords":
        assert parsed.lat is not None and parsed.lon is not None
        label = f"{parsed.lat}, {parsed.lon}"
        return GeocodeResult(parsed.lat, parsed.lon, label, "coordinates", [])
    if parsed.kind == "zip":
        assert parsed.zip is not None
        if zctas is None:
            raise AddressNotFoundError(
                f"Cannot locate ZIP {parsed.zip}: the ZIP boundary data is not loaded"
            )
        rows = zctas[zctas["zip"] == parsed.zip]
        if rows.empty:
            raise AddressNotFoundError(f"ZIP {parsed.zip} is not a known ZIP code (Census ZCTA)")
        row = rows.iloc[0]
        label = f"ZIP {parsed.zip} (approximate center)"
        return GeocodeResult(float(row["intpt_lat"]), float(row["intpt_lon"]), label, "zip", [])
    return geocoder.geocode(parsed.text)
