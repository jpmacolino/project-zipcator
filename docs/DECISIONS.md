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
