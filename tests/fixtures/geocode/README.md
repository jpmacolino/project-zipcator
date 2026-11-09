# Geocoder fixtures

Recorded from the live services on 2026-11-08 (one request each, polite, with the project's
descriptive `User-Agent`). They keep the unit tests offline (`responses` serves them) while
pinning the *real* response shapes.

| file | request |
|---|---|
| `census_match.json` | Census one-line address `401 E Jackson St, Tampa, FL 33602` (single match; `coordinates.x` = longitude, `y` = latitude) |
| `census_multi.json` | Census `100 Main St, Springfield` (several matches in different states) |
| `census_nomatch.json` | Census `zzzz qqqq nowhere 99999` (`addressMatches: []`) |
| `nominatim_match.json` | Nominatim `q=Tampa Theatre, Franklin St, Tampa`, `countrycodes=us`, `format=jsonv2`, `addressdetails=1` |

Variants (non-US result, malformed matches, HTTP errors) are derived from these in the tests,
not recorded, because Nominatim must not be hit with deliberately foreign queries.
Data © OpenStreetMap contributors (ODbL) for the Nominatim fixture; Census data is public domain.
