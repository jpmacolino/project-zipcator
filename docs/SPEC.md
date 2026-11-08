# Zipcator — Specification

**Status:** Approved for build · **Version:** 1.0 · **Date:** 2026-11-08
**Owner:** FloridaMenConsulting · **License:** MIT (public GitHub repo)

## 1. Summary

Zipcator is a small, local-first Python app with a Streamlit UI. A user enters a
US address and a radius in miles; the app returns every ZIP code (Census ZCTA)
whose area **overlaps** the radius circle, with the **percentage of each ZIP's
area inside the circle**, shown in a sortable table and on an interactive map,
exportable as CSV and as a copy-paste ZIP list for ad platforms.

**Primary use case:** local businesses / marketers deciding which ZIP codes to
target (direct mail, Meta/Google Ads ZIP targeting, flyering) around a location.

## 2. Goals and non-goals

### Goals
- Accurate overlap-based results (not centroid-only), so large rural ZIPs at the
  edge of the circle — and the ZIP the address itself is in — are never missed.
- Overlap % per ZIP plus a minimum-overlap filter so users can trim ZIPs that
  barely touch the circle.
- Runs locally on Windows, macOS and Linux with one setup command and one data
  build command.
- Repo is small and redistributable: no large data files committed; all bundled
  data sources are public domain or attribution-compatible with MIT.
- Thorough automated test suite; CI runs on every push.

### Non-goals (v1)
- Non-US addresses.
- USPS-exact delivery ZIPs (PO-box-only and single-organization ZIPs have no
  ZCTA polygon; documented limitation).
- Drive-time / isochrone radii.
- User accounts, persistence, or a hosted multi-user service (the app should be
  *deployable* to Streamlit Community Cloud, but that is not a v1 deliverable).
- Batch processing of many addresses (possible v2).

## 3. Background: ZIP codes vs ZCTAs

USPS ZIP codes are mail delivery routes, not polygons. The Census Bureau's
**ZIP Code Tabulation Areas (ZCTAs)** are polygons built from census blocks that
approximate residential ZIP coverage. ZCTAs are defined once per decennial
census, so the **2020 ZCTA5 (ZCTA520)** vintage is current until the 2030
census. ~33,800 ZCTAs cover the 50 states, DC and Puerto Rico.

Throughout this spec "ZIP" means "5-digit ZCTA". The UI and README must state
this plainly in one sentence, including that PO-box-only / unique ZIPs are not
included.

## 4. Data

All data is downloaded and processed by `scripts/build_data.py` into
`data/` (gitignored). Nothing under `data/` is committed except `data/README.md`.

### 4.1 Sources

| # | Dataset | Use | License | Required |
|---|---------|-----|---------|----------|
| D1 | Census **cartographic boundary** file, ZCTA520, 2020, 1:500k (`cb_2020_us_zcta520_500k.zip`, under `https://www2.census.gov/geo/tiger/GENZ2020/shp/`) | Polygons, **clipped to shoreline** (so ocean area doesn't distort overlap %) | Public domain | Yes |
| D2 | Census 2020 **ZCTA-to-County relationship file** (`tab20_zcta520_county20_natl.txt`, under `https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/`) | State + primary county per ZCTA (county with largest land overlap) | Public domain | Yes |
| D3 | **GeoNames US postal codes** (`https://download.geonames.org/export/zip/US.zip`) | Human-readable place/city name per ZIP | CC BY 4.0 — attribution required in README and app footer | Yes (city name column may be blank if missing) |
| D4 | Census **ACS 5-year** population by ZCTA (table `B01003_001E`) via `api.census.gov` | Population column + estimated population in radius | Public domain | **Optional** (P2) — build must succeed without it |

**Verify before relying on them:** URLs and file names above are believed
correct but must be verified at build time. If a URL has moved, find the current
official Census/GeoNames location, update `scripts/build_data.py`, and note it in
`docs/DECISIONS.md`. Do not substitute non-official mirrors or datasets with
restrictive licenses (e.g., no "free tier" commercial ZIP databases).

### 4.2 Processed artifact

`data/zcta.parquet` — GeoParquet, CRS EPSG:4326, one row per ZCTA:

| column | type | notes |
|---|---|---|
| `zip` | str (5 chars, zero-padded) | primary key; **never** an int (leading zeros, e.g. `02108`) |
| `state` | str (2-letter USPS abbr) | from D2 (state FIPS → abbr mapping in code) |
| `county` | str | primary county name, from D2 |
| `city` | str or null | from D3 |
| `land_area_sq_mi` | float | computed from geometry in an equal-area CRS (EPSG:5070 for CONUS is acceptable; use per-row local LAEA or document error for AK/HI/PR) **or** ALAND from source if present — pick one, document it |
| `intpt_lat`, `intpt_lon` | float | representative point (`shapely` `representative_point()`), guaranteed inside polygon |
| `population` | int or null | D4, optional |
| `geometry` | (Multi)Polygon | simplified, see below |

Also write `data/metadata.json`: source URLs, download timestamps, source
vintages, row count, simplification tolerance, build script version.

**Simplification:** apply `simplify(tolerance, preserve_topology=True)` so the
artifact is ≤ ~50 MB and loads in < 5 s on a typical laptop. Choose a tolerance
empirically and document it in `docs/DECISIONS.md`, with a measured comparison
of overlap % (simplified vs unsimplified) on at least 3 test scenarios. Max
acceptable overlap-% drift from simplification: **±1 percentage point**.

**Raw downloads** cache in `data/raw/` (gitignored); re-running the build reuses
them unless `--refresh` is passed.

### 4.3 Data build CLI

```
uv run python scripts/build_data.py [--refresh] [--with-population] [--census-api-key KEY]
```
- Idempotent; clear progress output; non-zero exit + actionable message on
  network failure or schema mismatch.
- Validates output: ≥ 33,000 rows; all `zip` match `^\d{5}$`; no null/empty
  geometries; all geometries valid (`make_valid` if needed); every row has a
  state.
- Core logic lives in `zipcator.data_build` (importable, testable); the script is
  a thin wrapper.

## 5. Functional requirements

### 5.1 Inputs (UI)
- **Address** text field (free-form one-line US address). Also accept:
  - a bare 5-digit ZIP → use that ZCTA's representative point as center (label
    clearly: "Center: ZIP 33602 (approximate center)");
  - `lat, lon` decimal coordinates (e.g. `27.9506, -82.4572`) → used directly.
- **Radius** in miles: number input + slider, range **0.5–100**, step 0.5,
  default **10**.
- **Minimum overlap %** slider, 0–100, default **0** (= any overlap).
- **Search** button. Results do not recompute on every keystroke.

### 5.2 Geocoding (`zipcator.geocode`)
1. **Primary:** US Census Geocoder, one-line address endpoint
   (`https://geocoding.geo.census.gov/geocoder/locations/onelineaddress`,
   `benchmark=Public_AR_Current`, `format=json`). No key.
2. **Fallback:** Nominatim (`https://nominatim.openstreetmap.org/search`), only
   if Census returns no match or errors. Requirements per Nominatim usage
   policy: `countrycodes=us`, a descriptive `User-Agent` (configurable via env
   var `ZIPCATOR_USER_AGENT`, default `zipcator/<version> (+<repo url>)`),
   ≤ 1 request/second, results cached. Fallback can be disabled via
   `ZIPCATOR_DISABLE_NOMINATIM=1`.
3. Timeouts (10 s) and one retry with backoff for transient errors.
4. Return a typed result: `GeocodeResult(lat, lon, matched_address, source,
   candidates: list)`. If multiple candidates, the UI lets the user pick one
   (default: first/best).
5. Clear errors: "Address not found", "Geocoding service unavailable — try
   again or enter coordinates", "Location is outside the US".
6. In-process cache (e.g. `functools.lru_cache` or `st.cache_data`) keyed by
   normalized address.
7. Geocoding is behind an interface/protocol so tests inject fakes; **unit tests
   never hit the network**.

### 5.3 Radius search (`zipcator.search`)
Given `center (lat, lon)` and `radius_mi`:

1. **Build the circle geodesically**: use `pyproj.Geod(ellps="WGS84").fwd` from
   the center at ≥ 128 evenly spaced azimuths to get a lon/lat polygon.
2. **Candidate prefilter**: spatial index (`GeoDataFrame.sindex.query`) using
   the circle's bounding box. Must be fast (< 50 ms for 100 mi).
3. **Project** candidates and circle to a local **Lambert Azimuthal Equal-Area**
   CRS centered on the search point (`+proj=laea +lat_0=<lat> +lon_0=<lon>
   +datum=WGS84 +units=m`). Area math happens here.
4. For each candidate: `overlap_area = area(zcta ∩ circle)`; keep rows with
   `overlap_area > 0` (tiny epsilon, e.g. > 1 m², to drop floating-point
   slivers — document the epsilon).
5. Compute per result row:
   - `overlap_pct` = overlap_area / zcta_area × 100 (0–100, rounded to 1 dp for
     display, full precision internally);
   - `area_in_radius_sq_mi`;
   - `zip_area_sq_mi`;
   - `center_distance_mi` = geodesic distance center → representative point;
   - `nearest_distance_mi` = geodesic-ish distance center → nearest point of the
     polygon (0 if the polygon contains the center);
   - `contains_center` bool;
   - `est_population_in_radius` = population × overlap_pct/100 when population
     is available (label in UI as a rough estimate assuming uniform density).
6. Apply `min_overlap_pct` filter, **except** the ZIP containing the center is
   always included (flagged) even if it falls below the threshold.
7. Sort: `contains_center` first, then `nearest_distance_mi`, then `zip`.
8. Return a `SearchResult` with the rows (DataFrame or list of dataclasses),
   the circle geometry (EPSG:4326, for the map), and summary stats: count,
   total area in radius, total est. population (if available).

**Edge cases that must be handled and tested:**
- Center in water / not inside any ZCTA (coastal, lake): no "home" ZIP; still
  return overlaps.
- Radius so small it lies entirely inside one ZIP → exactly one result, ~small
  overlap %.
- Circle crossing state lines (results span states).
- Alaska/Hawaii/Puerto Rico (LAEA centered on the point keeps this correct).
- Antimeridian (Aleutians): either handle correctly or reject searches whose
  circle crosses ±180° with a clear message. Document the choice.
- MultiPolygon ZCTAs (islands): overlap computed over all parts.
- No results (e.g., center far offshore): friendly empty state.

### 5.4 Outputs (UI)
- **Summary line:** "42 ZIP codes overlap a 10-mile radius around <matched
  address>" + total est. population if available.
- **Table** (`st.dataframe`), columns: ZIP, City, County, State, Overlap %,
  Area in radius (sq mi), Nearest distance (mi), Center distance (mi), Contains
  address, [Population, Est. pop in radius]. Sortable. ZIPs shown as text.
- **Map** (folium via `streamlit-folium`):
  - center marker with matched address tooltip;
  - radius circle drawn from the **geodesic polygon** (not a folium `Circle`
    approximation), so map and math match;
  - ZIP polygons for results, filled with a sequential color scale by overlap %,
    with tooltip: ZIP, city, overlap %;
  - ZIPs below the min-overlap threshold optionally shown as grey outlines
    (toggle);
  - auto-fit bounds to the circle.
- **Exports:**
  - "Download CSV" (all columns, ZIPs as text — must open correctly in Excel
    without dropping leading zeros; test this, e.g. by quoting);
  - "Copy ZIP list" text area containing comma-separated ZIPs (and a
    newline-separated option) for pasting into ad platforms.
- **Footer:** data sources + GeoNames CC BY 4.0 attribution + "ZIP = Census
  ZCTA" note + data build date from `metadata.json`.
- **Missing data:** if `data/zcta.parquet` doesn't exist, the app shows a clear
  instruction to run the build script (no stack trace).

### 5.5 CLI (secondary, small)
`uv run zipcator "123 Main St, Tampa, FL" --radius 10 [--min-overlap 5] [--csv out.csv]`
prints a table. Reuses the same core; exists mainly for scripting and to make
end-to-end testing easy.

## 6. Non-functional requirements
- **Performance:** data load ≤ 5 s cold (cached afterwards via
  `st.cache_resource`); search ≤ 1 s for a 100-mile radius in a dense metro
  (excluding geocoding latency). Add a benchmark test (marked `slow`).
- **Platforms:** Windows 10/11 first-class (primary dev machine), plus Linux
  and macOS. CI on `ubuntu-latest` and `windows-latest`.
- **Python:** 3.11+ (managed by `uv`).
- **Code quality:** `ruff check` + `ruff format --check` clean; `mypy` clean on
  `src/` (reasonable strictness, third-party stubs ignored where missing).
- **Logging:** `logging` module, no prints in library code.
- **Config:** env vars only (`ZIPCATOR_DATA_DIR`, `ZIPCATOR_USER_AGENT`,
  `ZIPCATOR_DISABLE_NOMINATIM`, `CENSUS_API_KEY`). No secrets in repo.

## 7. Architecture

```
src/zipcator/
  __init__.py        # __version__
  config.py          # env-var config, paths
  models.py          # dataclasses: GeocodeResult, ZipMatch, SearchResult
  geo.py             # geodesic circle, LAEA projection helpers, distances
  geocode.py         # Geocoder protocol, CensusGeocoder, NominatimGeocoder, ChainGeocoder, input parsing (ZIP / lat,lon)
  data.py            # load_zctas(path) -> GeoDataFrame (+ validation)
  data_build.py      # download, join, simplify, validate, write parquet
  search.py          # find_zips(gdf, center, radius_mi, min_overlap_pct) -> SearchResult
  export.py          # CSV + ZIP-list formatting
  cli.py             # CLI entry point
app/
  streamlit_app.py   # UI only; thin layer over the library
scripts/
  build_data.py      # wrapper around zipcator.data_build
tests/
  unit/              # no network, no real data; synthetic fixtures
  integration/       # need data/zcta.parquet (marker: data)
  live/              # hit real APIs (marker: network)
  ui/                # Streamlit AppTest + optional Playwright screenshots
  fixtures/          # small recorded API responses, tiny synthetic geodata
docs/
  SPEC.md, DECISIONS.md, QA_REPORT.md
```

Rule: **all logic lives in `src/zipcator`**; `app/streamlit_app.py` contains
layout and wiring only, so nearly everything is unit-testable.

## 8. Testing and QA strategy

This build is explicitly a test of a full write → test → fix → QA loop.
Tests are first-class deliverables.

### 8.1 Test layers and markers
| Layer | Marker | Runs by default / in CI | Notes |
|---|---|---|---|
| Unit | (none) | Yes | Synthetic data, mocked HTTP, fast (< 30 s total) |
| Property | (none) | Yes | `hypothesis` |
| UI | (none) | Yes | `streamlit.testing.v1.AppTest`, geocoder + data mocked |
| Integration | `data` | No (local, after data build) | Real `data/zcta.parquet` |
| Live | `network` | No (manual) | Real Census / Nominatim calls, few and polite |
| Benchmark | `slow` | No | Performance budgets from §6 |
| Visual QA | `visual` | No | Playwright screenshots of running app for human/agent review |

`pyproject.toml` configures markers; default `addopts` excludes
`data`, `network`, `slow`, `visual`.

### 8.2 Required unit/property tests (minimum)
**geo**
- Geodesic circle: projected area within 0.5% of πr² for r ∈ {0.5, 10, 100} mi
  at latitudes {0°, 28°, 45°, 64°}.
- Known geodesic distances match within 0.5% (use reference pairs computed
  independently, e.g. via `geographiclib` or published values).
**search** (synthetic ZCTAs: a grid of known squares/shapes built in fixtures)
- Polygon fully inside circle → 100%. Disjoint → excluded. Polygon split by
  circle edge → overlap matches analytic/independently computed value within 0.5 pp.
- Large polygon containing the center but whose representative point is
  outside the radius → **included** with `contains_center=True` (the key
  rural regression test).
- Large polygon whose edge enters the circle but whose centroid is far outside
  → included (the reason we chose overlap over centroid).
- `min_overlap_pct` filtering works; home ZIP exempt from the filter.
- MultiPolygon handling; center-in-water (no home ZIP); empty results.
- Sorting order per §5.3.
- **Properties (hypothesis):** overlap_pct ∈ [0, 100]; results for radius r1 ⊆
  results for r2 when r1 < r2; each ZIP's overlap_pct is non-decreasing in
  radius; no duplicate ZIPs; ZIPs always 5-char strings.
**geocode** (HTTP mocked with `responses`, using recorded JSON fixtures)
- Census match, Census no-match → Nominatim fallback, both fail, timeout +
  retry, multiple candidates, non-US Nominatim result rejected, fallback
  disabled via env var, User-Agent header present, rate limit respected
  (inject a fake clock — no real sleeps in tests).
- Input parsing: ZIP-only, `lat, lon` (valid/invalid ranges, extra spaces,
  negative values), plain address.
**data_build / data**
- Pipeline runs on tiny synthetic inputs (written to a temp dir in the shape of
  D1/D2/D3) → correct schema, zero-padded ZIPs, primary county = largest
  overlap, missing city tolerated, validation rejects bad rows.
- `load_zctas` raises a clear custom error when the file is missing.
**export**
- CSV preserves leading zeros; ZIP list formatting (comma, newline); empty input.
**cli**
- Arg parsing, error exit codes, CSV output (with geocoder mocked).

### 8.3 Integration tests (marker `data`) — golden scenarios
Build these against the real artifact. For each, the expected results must be
established by **independent verification** (e.g., computing overlap with the
unsimplified D1 shapes, manually inspecting the map), not by snapshotting
whatever the code outputs first. Record reasoning in `docs/QA_REPORT.md`.
1. Dense urban — downtown Tampa, FL, 5 mi.
2. Rural/large ZIPs — a rural point in interior Florida (e.g., near Okeechobee)
   or rural Montana, 10 mi: demonstrate a ZIP that centroid-only would miss.
3. Coastal — Miami Beach, FL, 5 mi: ocean doesn't inflate overlap; center may
   sit near water.
4. State border — a point near a state line (e.g., Texarkana or St. Marys, GA /
   FL line), 15 mi: results span states.
5. Tiny radius — 0.5 mi inside a large ZIP → 1 result.
6. Big radius — 100 mi from Orlando: performance + sanity of count.
7. Non-CONUS — Honolulu, HI and San Juan, PR, 10 mi.
8. Leading-zero ZIP region — Boston, MA, 3 mi (ZIPs like `02108`).

### 8.4 UI tests
- `AppTest`: happy path (enter address → results table and map rendered),
  geocode failure message, missing-data message, ZIP-only input, lat/lon input,
  min-overlap slider changes table, CSV download button present.
- Visual QA (`visual`, manual): Playwright launches `streamlit run`, performs
  scenarios 1–3, saves screenshots to `qa/screenshots/` (gitignored). The
  builder must **look at the screenshots** and fix layout problems.

### 8.5 Coverage and gates
- Coverage ≥ 90% line coverage on `src/zipcator` (excluding `cli.py` main
  guard); report via `pytest-cov`.
- CI (GitHub Actions): `ruff check`, `ruff format --check`, `mypy src`,
  `pytest` (default markers) on ubuntu + windows, Python 3.11 and 3.12.

### 8.6 QA process
- Bugs found during testing/QA are logged in `docs/QA_REPORT.md` (symptom, root
  cause, fix, regression test name). Every bug fix gets a regression test.
- An **independent QA review** (fresh-context reviewer subagent, see
  `.claude/agents/qa-reviewer.md`) is run at the end of each phase; its
  findings are triaged and either fixed or explicitly deferred with reasoning.

## 9. Acceptance criteria (v1 done)
1. Fresh clone on Windows: `uv sync` → `uv run python scripts/build_data.py` →
   `uv run streamlit run app/streamlit_app.py` works, following only the README.
2. All 8 golden scenarios pass and are documented with independent
   verification notes.
3. The rural regression (§8.2) is demonstrated on real data in
   `docs/QA_REPORT.md`, comparing overlap vs centroid-only results.
4. Unit + UI suite green on CI (ubuntu + windows), coverage ≥ 90%, lint/type clean.
5. Map circle visually matches the computed radius; screenshots reviewed.
6. README: what it does, screenshot, setup, data build, ZIP-vs-ZCTA limitation,
   data sources + licenses/attribution, Nominatim usage note, MIT license.
7. Repo contains no data files > 1 MB and no secrets.

## 10. Phased plan
| Phase | Scope | Exit criteria |
|---|---|---|
| 0 | Environment: `uv sync`, `git init`, verify tooling, CI skeleton green | `uv run pytest` runs (0 tests OK), ruff/mypy run |
| 1 | `geo` + `search` on synthetic data (TDD) | §8.2 geo/search tests green |
| 2 | `data_build` + `data`; build real artifact; simplification study | artifact built + validated; DECISIONS.md updated; `data` smoke test green |
| 3 | `geocode` + input parsing (TDD, mocked) + a few `network` tests run once manually | §8.2 geocode tests green; live tests pass |
| 4 | `export`, `cli` | tests green; CLI works end-to-end on real data |
| 5 | Streamlit app + map | AppTest suite green; visual QA screenshots reviewed |
| 6 | Golden scenarios, benchmarks, full QA review, README, polish | §9 acceptance criteria all met |

## 11. Future ideas (not v1)
- Batch mode (CSV of addresses → ZIPs per address).
- Population-weighted overlap using census block populations (more accurate
  than uniform-density estimate).
- Drive-time radius via an isochrone API.
- Publish the processed artifact as a GitHub Release asset so users can skip the
  build.
- Hosted demo on Streamlit Community Cloud (Census geocoder only).
- Supplement with point-only ZIPs (PO boxes) flagged separately.
