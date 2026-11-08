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
  point: radial distances there are true geodesic distances, so planar distance center→polygon
  is the exact geodesic nearest distance (LAEA is only used for area).
- **2026-11-08 — Min-overlap filter is `>=` with a 1e-9 pp tolerance**; the home ZIP(s) are always
  kept. If the center sits exactly on a shared boundary, both ZIPs are flagged
  `contains_center`.
- **2026-11-08 — Sort ties:** nearest distance is rounded to 1e-6 mi before sorting so float
  noise cannot defeat the ZIP tie-break (SPEC §5.3 step 7).
- **2026-11-08 — Search returns `excluded`** (ZIPs overlapping but below the threshold) alongside
  `matches`, to feed the optional grey outlines on the map (SPEC §5.4).
- **2026-11-08 — CI `push` trigger limited to `main`.** PR branches are covered by the
  `pull_request` trigger, avoiding duplicate runs.
