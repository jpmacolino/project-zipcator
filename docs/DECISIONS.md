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
- **2026-11-08 — CI `push` trigger limited to `main`.** PR branches are covered by the
  `pull_request` trigger, avoiding duplicate runs.
