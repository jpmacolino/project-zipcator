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
- **Simplification: size-adaptive, not uniform (SPEC §4.2 study).** Measured on the real data:
  max overlap-% drift vs the *unsimplified* shapes over 7 scenarios (Tampa 5 mi, Okeechobee
  rural 10 mi, Miami Beach 5 mi, Boston 3 mi, Honolulu 10 mi, San Juan 10 mi, Orlando 100 mi);
  "ZIP diff" = ZIPs present in one result but not the other (0 everywhere):

  | simplification | parquet MB | load s | Tampa | Okeechobee | Miami Beach | Boston | Honolulu | San Juan | Orlando 100 mi |
  |---|---|---|---|---|---|---|---|---|---|
  | uniform 0.0002° | 86.7 | 0.52 | 0.118 | 0.001 | 0.457 | 0.171 | 0.026 | 0.149 | 0.147 |
  | uniform 0.0005° | 69.7 | 0.28 | 0.201 | 0.006 | 0.317 | **0.960** | 0.188 | 0.446 | 0.422 |
  | uniform 0.001° | 48.9 | 0.22 | 0.298 | 0.076 | **1.297** | **2.372** | 0.474 | **1.665** | **1.242** |
  | **adaptive rel 0.01, [0.0001°, 0.003°] (chosen)** | **43.7** | 0.24 | 0.171 | 0.087 | 0.152 | 0.138 | 0.212 | 0.226 | **0.239** |
  | adaptive rel 0.02, [0.0001°, 0.003°] | 32.3 | 0.17 | 0.522 | 0.123 | 0.457 | 0.617 | 0.474 | 0.358 | 0.558 |

  No uniform tolerance meets both limits (≤ ~50 MB *and* ±1 pp): 0.0005° is 69.7 MB, 0.001° drifts
  up to 2.4 pp. Drift in percentage points is worst on small urban ZIPs while the vertices sit in
  large rural ZIPs, so tolerance is now `rel × √area` (latitude-corrected) clipped to
  [0.0001°, 0.003°]. Chosen `rel = 0.01`: 43.7 MB, worst drift 0.24 pp (4× margin to the ±1 pp
  limit), load 0.24 s. Reproduce with `uv run python scripts/simplification_study.py`.
  Alternatives rejected: uniform tolerance (above); `rel = 0.02` (smaller but only 1.6× margin);
  coordinate rounding/zstd (changes file format for no accuracy benefit; snappy default is fine).
  Residual risk: drift was measured on 7 scenarios; the phase 6 golden scenarios re-check it.
- **`BUILD_VERSION = "2"`** (1 → 2 when adaptive simplification replaced the uniform tolerance).
- **Raw download timestamps** are stored in `<file>.meta.json` sidecars written at download time;
  files placed in `data/raw` by hand fall back to the file's mtime.
- **Default ACS vintage 2023** (`B01003_001E`, ACS 5-year); override via `acs_year` in the library.
  Not exercised against the live API (needs a key); covered by mocked tests only.
