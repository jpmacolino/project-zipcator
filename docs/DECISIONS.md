# Decisions log

Format: date — decision — why — alternatives considered.

## Made during scoping (2026-11-08)
- **US only.** Matches use case; Census data is US-only.
- **Streamlit UI with a folium map.** Fastest path to a good local UI; runs on Windows.
- **Overlap-based matching with overlap %, not centroid-in-circle.** Centroid-only
  misses large rural ZIPs at the circle edge and can omit the address's own ZIP.
  Overlap % + a min-overlap filter controls over-inclusion of ZIPs that barely touch.
- **ZIP = Census 2020 ZCTA5.** Free, public domain, polygons; ZCTAs are fixed until
  the 2030 census. Limitation: PO-box-only / unique ZIPs not included.
- **Cartographic boundary (shoreline-clipped) polygons** so ocean doesn't inflate overlap.
- **Census Geocoder primary, Nominatim fallback.** Both free; Nominatim used politely per
  its usage policy and can be disabled.
- **Data is built locally, not committed.** Keeps repo small (GitHub 100 MB file limit)
  and refreshable.
- **MIT license.** All dependencies permissive; data public domain except GeoNames
  (CC BY 4.0, attribution in README + app footer).
- **Population (ACS) is optional (P2).** Useful for marketing but not required for v1.

## Made during build
<!-- Claude Code appends here -->
- **2026-11-08 — `uv` invoked via `%USERPROFILE%\.local\bin`.** It is installed but not on
  PATH in the agent shells; each session prepends it. Not a repo change. Alternative:
  reinstalling uv (unnecessary).
- **2026-11-08 — `uv.lock` exempt from the 1 MB tracked-file gate.** SPEC §9.7 targets
  *data* files; the lockfile is generated and was already ~0.58 MB. Every other tracked
  file must stay ≤ 1,000,000 bytes (`scripts/check.py`). Alternative: no exemption (would
  fail once dependencies grow).
- **2026-11-08 — `network`/`visual` tests are opt-in by `-m` content, `data` tests fail
  loudly without the artifact.** Why: a CLI `-m` overrides `addopts`; silent skips would
  let `-m data` go green while testing nothing. Alternative: skip with a message (rejected).
- **2026-11-08 — Antimeridian/pole policy: reject, don't split.** A circle that crosses ±180°
  or encloses a pole raises `AntimeridianError` ("not supported; use a smaller radius").
  Why: the only affected US area is the far-western Aleutians; splitting adds complexity
  for ~no users. Alternative: split the circle into two polygons (deferred to v2).
- **2026-11-08 — Circle has 360 vertices (1° azimuth steps).** SPEC asks ≥128. Chord error of
  a 360-gon is ~0.002% of area vs 0.04% for 128, at negligible cost.
- **2026-11-08 — Overlap epsilon is 1 m²** (`search.MIN_OVERLAP_AREA_M2`). Drops zero-area
  contacts / float slivers; ~1e-9 of the smallest legal circle (0.5 mi ≈ 8e5 m²).
- **2026-11-08 — `zip_area_sq_mi` is the *projected polygon area* (LAEA), not `land_area_sq_mi`.**
  Why: overlap % must be area-in-radius / the same polygon's area, so it stays in [0, 100].
- **2026-11-08 — `nearest_distance_mi` measured in an azimuthal-equidistant CRS** centred on the
  point: radial distances there are true geodesic distances from the center, so planar
  distance center→polygon is the geodesic nearest distance for the polygon *as stored*
  (ZIP edges are straight in lon/lat, so this is exact to ~1e-4 relative at 100 mi; QA
  phase 1 corrected an earlier "exact" claim). LAEA is only used for area.
- **2026-11-08 — Min-overlap filter is `>=` with a 1e-9 pp tolerance**; the home ZIP(s) are always
  kept. If the center sits exactly on a shared boundary, both ZIPs are flagged
  `contains_center`.
- **2026-11-08 — Sort ties:** nearest distance is rounded to 1e-6 mi before sorting so float
  noise cannot defeat the ZIP tie-break (SPEC §5.3 step 7).
- **2026-11-08 — Search returns `excluded`** (ZIPs overlapping but below the threshold) alongside
  `matches`, to feed the optional grey outlines on the map (SPEC §5.4).
- **2026-11-08 — CI `push` trigger limited to `main`.** PR branches are covered by the
  `pull_request` trigger, avoiding duplicate runs.

## Phase 2 — data build (2026-11-08)
- **Source URLs verified 2026-11-08** (all HTTP 200 at the spec'd locations): D1
  `cb_2020_us_zcta520_500k.zip` (66.7 MB, 33,791 ZCTAs, NAD83/EPSG:4269, all geometries valid),
  D2 `tab20_zcta520_county20_natl.txt` (UTF-8 BOM, pipe-delimited; 903 leading/trailing rows are
  counties with no ZCTA and are dropped), D3 `US.zip` (headerless TSV), D4 Census API. No URL
  needed changing.
- **Census API now requires a key.** Without one `api.census.gov` answers HTTP 200 with an HTML
  "Missing Key" page. The build therefore (a) refuses `--with-population` without a key *before*
  downloading anything, (b) detects the HTML/non-JSON reply and says how to get a free key, and
  (c) scrubs the key from every error message. Population stays optional (SPEC §4.1 D4).
- **GeoNames territory files added (D3).** `US.zip` has no territory ZIPs: all 149 blank-city
  ZCTAs were PR (132), GU (7), VI (6), MP (3), AS (1). The small `PR/VI/GU/AS/MP.zip` files from the
  same official location (same CC BY 4.0 licence) fill ZIPs that `US.zip` lacks; `US.zip` wins
  on conflicts. Result: 0 blank cities in the real artifact. Alternative: leave PR blank
  (rejected: golden scenario 7 is San Juan).
- **`land_area_sq_mi` = Census `ALAND20`** (m² → sq mi), not recomputed from geometry. Why: it is
  the Census's own land area, unaffected by our simplification or by shoreline clipping. Search
  does *not* use it: overlap % divides by the projected polygon area (see phase 1 decisions).
- **Primary county** = largest `AREALAND_PART`; ZCTAs with no land overlap fall back to largest
  `AREAWATER_PART`, then lowest county GEOID (deterministic). **State** = state FIPS of that
  county (so a ZCTA straddling a state line gets its primary county's state). State FIPS map
  covers 50 states, DC, PR, AS, GU, MP, VI.
- **Geometry in the artifact** is repaired with `make_valid`, reduced to polygonal parts, and rows
  that end up empty are dropped with a warning (none were dropped in the real build). Rows with
  no state are likewise dropped with a warning; the SPEC §4.3 validation (≥ 33,000 rows, 5-digit
  unique text ZIPs, no null/empty/invalid geometry, state on every row) runs *before* the
  artifact replaces an existing one; files are written via temp + rename.
- **Representative point** is computed from the *simplified* geometry, so it is guaranteed inside
  the stored polygon (checked for all 33,791 rows in the `data` smoke test).
- **Simplification: coverage simplification, uniform 0.0005° (SPEC §4.2 study; revised after
  QA phase 2).** Measured on the real data: max overlap-% drift vs the *unsimplified* shapes
  over 7 scenarios (Tampa 5 mi, Okeechobee rural 10 mi, Miami Beach 5 mi, Boston 3 mi, Honolulu
  10 mi, San Juan 10 mi, Orlando 100 mi), artifact size
  with the default (snappy) and the chosen (zstd-19) parquet compression, and the total area by
  which *neighbouring ZIPs overlap each other* (the raw Census file has 0.000 km²):

  | method (tolerance) | MB snappy | MB zstd-19 | neighbour overlap km² | Tampa | Okee. | Miami B. | Boston | Honolulu | San Juan | Orlando 100 mi | worst pp |
  |---|---|---|---|---|---|---|---|---|---|---|---|
  | per-polygon `simplify` 0.0005° | 69.7 | 32.2 | **891** | 0.201 | 0.006 | 0.317 | 0.960 | 0.188 | 0.446 | 0.422 | 0.960 |
  | per-polygon `simplify` 0.001° | 48.9 | 23.9 | **2,329** | 0.298 | 0.076 | 1.297 | 2.372 | 0.474 | 1.665 | 1.242 | **2.372** |
  | coverage 0.0003° | 93.0 | 41.2 | 0 | 0.007 | 0.000 | 0.025 | 0.024 | 0.001 | 0.022 | 0.014 | 0.025 |
  | **coverage 0.0005° (chosen)** | 90.5 | **40.2** | **0** | 0.028 | 0.000 | 0.128 | 0.079 | 0.005 | 0.020 | 0.022 | **0.128** |
  | coverage 0.001° | 77.0 | 35.2 | 0 | 0.129 | 0.005 | 0.416 | 0.524 | 0.111 | 0.217 | 0.560 | 0.560 |
  | coverage 0.0015° | 58.8 | 28.2 | 0 | 0.324 | 0.006 | 0.681 | 0.422 | 0.100 | 0.395 | 0.339 | 0.681 |

  Reproduce with `uv run python scripts/simplification_study.py` (~5 min).
  **History:** the first build used per-polygon Douglas-Peucker (`shapely.simplify`, described
  as "topology-preserving", which only prevents self-intersection). No *uniform* per-polygon
  tolerance met both spec limits (0.0005° = 69.7 MB snappy; 0.001° drifts 2.4 pp), so a
  size-adaptive tolerance (rel × √area, clipped) was adopted: 43.7 MB, worst drift 0.24 pp.
  The phase 2 QA review then found that *any* per-polygon scheme simplifies the two sides of a
  shared boundary differently, opening 9,198 km² of pairwise overlap (~0.12 % of area): a
  search center could fall in a gap (no home ZIP) or in two ZIPs. `shapely.coverage_simplify`
  simplifies every shared edge once, so the clean partition stays clean (0 km²) **and** it is
  ~7× more accurate at the same tolerance. With zstd-19 parquet (also fixes size) the adaptive
  scheme was removed. Chosen 0.0005°: 40.2 MB (≤ ~50 MB), worst drift 0.128 pp on the study
  scenarios (8× margin to ±1 pp), 0.37 pp worst in the reviewer's 543 independent searches on
  the earlier adaptive build; the `data` tests re-check drift (≤ 0.5 pp asserted, on six
  different scenarios; a ZIP present in only one result must be a < 0.05 % sliver), overlap
  (< 0.01 km²) and total area (±0.1 %) on every run.
  Alternatives rejected: coverage 0.001° (35 MB but 0.56 pp worst: less margin than needed);
  per-polygon schemes (gap/overlap defect); coordinate rounding (no accuracy benefit).
- **zstd (level 19) parquet compression** is the artifact default: about 45 % smaller than snappy
  for coordinate data; read speed is unaffected (load ≈ 0.3–0.5 s). pyarrow reads it natively.
- **Requires shapely ≥ 2.1** (`coverage_simplify`); `pyproject.toml` updated.
- **Build never silently drops ZCTAs.** A ZCTA with no state match (e.g. a new state FIPS) or no
  polygon area is kept through assembly and *fails the build* with its ZIP named and "Nothing was
  written" (SPEC §4.3 "every row has a state" would otherwise be vacuous). The real build keeps
  all 33,791 (checked against the raw shapefile's own ZIP list in the `data` tests).
- **Loader (`load_zctas`) checks** columns, EPSG:4326, text 5-digit unique ZIPs, known state
  codes, null/empty geometry, finite in-range `intpt_*` lying *inside* their polygon, and
  non-negative integer population. It does **not** re-check geometry validity (1.2 s of a 5 s
  load budget): validity is enforced by the build, and `find_zips` repairs defensively. It also
  enforces no minimum row count (tiny artifacts are loadable in tests; the build enforces
  ≥ 33,000).
- **Raw cache trust:** a cached raw file is reused only if its sidecar URL matches (or it has no
  sidecar, e.g. hand-placed); metadata records whether each timestamp is `download_sidecar` or
  `file_mtime`. Corrupt/empty cached files fail with a `--refresh` hint. Content is not hashed.
- **State label caveat (known, user-visible).** `state` is the state of the primary *county*
  (SPEC §4.2/D2), so 13 border ZCTAs disagree with their USPS/GeoNames state (e.g. 82930 Evanston
  is labelled UT, USPS says WY; 79837 Dell City NM vs TX; 86515 Window Rock NM vs AZ; 42223 Fort
  Campbell TN vs KY). Accepted for v1; to be noted in the README limitations (phase 6).
- **`land_area_sq_mi` vs polygon area (note for the UI, phase 5).** ALAND excludes inland water
  that the 1:500k polygons include, so 788 ZCTAs (2.3 %) differ by > 25 % from the projected
  polygon area (e.g. 34138 Chokoloskee: 0.28 vs 4.25 sq mi). The UI shows only *polygon* areas
  ("Area in radius") and must not present `land_area_sq_mi` next to them unlabeled.
- **Known, accepted:** `zcta.parquet` and `metadata.json` are replaced in two atomic steps, so a
  crash between them can leave stale metadata (very low risk; the footer only shows the date).
  `config.data_dir()` defaults to `<repo>/data` derived from the package location, which is only
  right for an editable/checkout install (the documented setup); `ZIPCATOR_DATA_DIR` overrides.
- **Census population (D4) is still unverified against the live API** (no key available in this
  environment); covered by mocked tests of the request/response shapes only.
- **`BUILD_VERSION = "3"`** (1 uniform per-polygon → 2 adaptive per-polygon → 3 coverage).
- **Raw download timestamps** are stored in `<file>.meta.json` sidecars written at download time;
  files placed in `data/raw` by hand fall back to the file's mtime.
- **Default ACS vintage 2023** (`B01003_001E`, ACS 5-year); override via `acs_year` in the library.
  Not exercised against the live API (needs a key); covered by mocked tests only.

## Phase 3 — geocoding and input parsing (2026-11-08)
- **Service behaviour verified live (2026-11-08) and recorded as fixtures:** Census returns
  `coordinates.x` = longitude and `y` = latitude; "no match" is HTTP 200 with
  `addressMatches: []` (not an error); ambiguous input returns several matches, best first (e.g.
  `100 Main St, Springfield` spans several states). Nominatim with `countrycodes=us` returns
  `address.country_code == "us"` for Puerto Rico too, so the US-only filter does not reject
  territories (one reviewer request, "Calle San Francisco, San Juan"). Census is the primary for GU/VI/AS/MP.
- **Fallback chain: the fallback's error wins.** If Census finds nothing and Nominatim is
  unreachable we raise "service unavailable" (we cannot honestly say "not found"); if Nominatim
  answers authoritatively ("not found" / "outside the US") that answer is used. Invalid input is
  never retried against the fallback.
- **Input rules (SPEC §5.1).** Text is NFKC-normalised (so fullwidth digits typed from a CJK
  IME work), whitespace-collapsed, capped at 300 characters, and classified: `^[0-9]{5}(-[0-9]{4})?$`
  is a ZIP (ZIP+4 is accepted and reduced to its ZIP); **two numbers and nothing else are always
  coordinates**, so an out-of-range pair (`95, 10`, `12345 67890`) is an error rather than a nonsense
  address lookup; anything else is an address. Digits are ASCII-only (`\d` matched Arabic-Indic
  digits). Degree symbols get a "use decimal degrees" hint; `N/S/E/W` suffixes are *not*
  detected (too many street addresses such as `123 N Main St`). Scientific notation, `NaN`,
  underscores and parenthesised pairs are treated as addresses.
- **Nominatim policy.** `countrycodes=us`, `User-Agent` from `ZIPCATOR_USER_AGENT` (default
  `zipcator/<version> (+<repo url>)`, resolved per request), `limit=5`, **one process-wide
  limiter** (the policy limit is per client, not per object) that also gates retries, 429/503
  retried once; a numeric `Retry-After` up to 30 s replaces the backoff and a longer one abandons
  the request. Results without a `country_code` are treated as foreign. An invalid User-Agent
  (CR/LF) is a logged configuration error, not a retried outage.
- **Timeouts/retries.** 10 s per request, `retries=1` with exponential backoff (negative values
  clamp to 0). The 10 s is per request, not a total deadline: worst case is about
  2 × 20 s per service. Accepted for v1.
- **"Location is outside the US" (SPEC §5.2.5) is raised in `locate()`** for coordinates and
  for geocoder results outside coarse US/territory bounding boxes (CONUS + near offshore,
  Alaska incl. the Aleutians east of 180°, Hawaii, PR/USVI, Guam/CNMI, American Samoa; ~1.5° margins).
  Why there: Census is US-only and Nominatim is filtered by `countrycodes=us`, so the error would
  otherwise be unreachable; it also catches a swapped lat/lon from a geocoder. **Limitation:** a
  box test cannot separate border towns (Toronto, Tijuana lie inside the CONUS box); those
  coordinates proceed to the search and give the friendly empty/limited-results state. Offshore
  points inside the margins also reach that empty state (SPEC §5.3).
- **Cache.** `CachedGeocoder`: bounded LRU keyed by the normalised query (casefolded, commas and
  whitespace ignored, so `1 Main St, Apt 2` and `1 Main St Apt 2` share an entry: harmless),
  failures never cached, results copied in and out (`candidates` is a mutable list), `maxsize=0`
  disables, negative is an error. `default_geocoder()` returns a *new* cache each call: **the
  Streamlit app must create it once** (`st.cache_resource`), otherwise the cache is useless
  (phase 5 requirement).
- **Known, accepted:** no single-flight protection (concurrent identical queries each call the
  service; the Nominatim limiter still serialises them); no response-size cap; Census results are only
  range- and US-box-checked, not plausibility-checked against the ZIP in the returned address.
- **Fixtures** are real recorded responses (tests/fixtures/geocode/README.md); non-US and
  malformed variants are derived in the tests because Nominatim must not be queried with
  deliberately foreign addresses. Live tests: 3 Census requests and 1 Nominatim request.
