# QA report

## Summary
<!-- Filled in at the end: test counts per layer, coverage %, bugs found/fixed,
     golden scenarios verified, open/deferred findings. -->

## Bug log
| # | Phase | Symptom | Root cause | Fix | Regression test |
|---|-------|---------|------------|-----|-----------------|
| 1 | 0 | `pytest -m "not slow"` ran live-API tests | A CLI `-m` replaces the `-m` in `addopts` | conftest hook skips `network`/`visual` unless `-m` names them | `test_overriding_m_does_not_enable_network_or_visual` |
| 2 | 0 | Test in `tests/integration/` missing `@data` would run in CI without data; missing artifact gave unclear errors | No directory→marker mapping; no artifact check | conftest marks by directory; `data` tests error with "run build_data.py" | `test_directory_implies_marker`, `test_data_marker_errors_with_instruction_when_artifact_missing` |
| 3 | 0 | `check.py` linted/typed the wrong dir when run from elsewhere; ignored unknown flags; no §9.7 enforcement | Relied on cwd; no arg validation | Runs with `cwd=REPO_ROOT`, rejects unknown args, adds 1 MB tracked-file gate | `test_unknown_flag_is_rejected`, `test_oversized_flags_big_tracked_file` and 3 siblings |
| 4 | 1 | `find_zips` accepted an int `zip` column and returned `'2108'` (leading zero lost); also crashed deep in `to_crs` on `crs=None` and passed duplicate ZIPs through | No input validation of the ZIP column / CRS | `_validate_zips` (string dtype, `^\d{5}$`, unique, on candidates only); `crs=None` assumed EPSG:4326 | `test_integer_zip_column_is_rejected_not_silently_unpadded`, `test_malformed_zip_strings_are_rejected`, `test_duplicate_zips_are_rejected`, `test_crs_none_is_assumed_wgs84` |
| 5 | 1 | (test gap) sindex prefilter could be bypassed, or replaced by a naive degree bbox, with all tests green | No test observed the prefilter or the circle's extreme tips | Debug log of candidate counts; slivers at E/W/N/S tips | `test_prefilter_discards_far_zips_before_the_expensive_work`, `test_slivers_at_the_four_extreme_tips_of_the_circle_are_found` (verified by mutation: both mutants now fail) |
| 6 | 1 | (test gap) sorting by `center_distance_mi` instead of `nearest_distance_mi` still passed | Test data where both orders coincide | Case with huge ZIP: near edge, far representative point | `test_nearest_edge_distance_not_representative_point_distance_decides_order` |
| 7 | 2 | Real artifact had 9,198 km² (~0.12 %) of overlap between neighbouring ZIPs; a search center could fall in a gap (no home ZIP) or in two ZIPs (probe: 0.08 % of points had no home, 0.18 % the wrong one, 1.6 % two) | Per-polygon Douglas-Peucker simplifies the two sides of a shared boundary independently; the docs called it "topology-preserving" | `shapely.coverage_simplify` (shared edges simplified once), uniform 0.0005°, zstd parquet; adaptive scheme removed | `test_coverage_simplification_keeps_neighbours_a_clean_partition`, `test_per_polygon_simplification_would_have_failed_that_test`, `test_neighbouring_zips_do_not_overlap_after_simplification` (data) |
| 8 | 2 | ZCTAs with no state or polygon were silently dropped (log line only), so "every row has a state" validation was vacuous and `min_rows` tolerated 791 losses; probe: bad FIPS gave "build OK, rows 4 (5 input zctas)" | `assemble()` filtered rows before validation | Rows kept; validation fails the build naming the ZIPs; `input_zcta_count` in metadata | `test_build_fails_loudly_naming_zips_that_have_no_state`, `test_assemble_keeps_rows_without_a_state_so_validation_can_flag_them`, `test_no_zcta_was_dropped_versus_the_raw_boundary_file` (data) |
| 9 | 2 | `load_zctas` accepted NaN / swapped `intpt_lat/lon`, state `XX`, negative and non-numeric population (silently NA); CRS error dumped PROJJSON | Thin loader validation | Checks added (see DECISIONS.md); short CRS message | `test_nan_representative_point_is_rejected`, `test_swapped_lat_lon_representative_points_are_rejected`, `test_unknown_state_codes_are_reported`, `test_negative_population_is_reported`, `test_non_numeric_population_is_a_format_error_not_silently_blank`, `test_wrong_projected_crs_error_names_the_crs_briefly` |
| 10 | 2 | Empty/corrupt cached raw file gave a raw `EmptyDataError` traceback; Census key reachable via `__cause__`/`__context__`; cache reused even if the source URL changed | Narrow exception handling; raise-from inside `except`; cache keyed by file name only | `BuildError` + `--refresh` hint; raise outside `except`; sidecar URL check; timestamp provenance in metadata | `test_empty_cached_relationship_file_is_a_build_error_with_refresh_hint`, `test_census_key_is_absent_from_the_whole_exception_chain`, `test_cache_from_a_different_url_is_not_reused` |

## Golden scenario verification (SPEC §8.3)
<!-- For each scenario: inputs, expected results, HOW they were independently verified. -->

## Rural regression: overlap vs centroid-only (SPEC §9.3)

## Phase reviews
<!-- One entry per /qa-review run: findings, triage, gate results. -->

### Phase 0 — environment and scaffold (tag `phase-0`)
Reviewer verdict: phase can close (0 HIGH, 2 MEDIUM, 6 LOW groups).

| Finding | Triage |
|---|---|
| M1 `-m` override re-enables network tests | **Fixed** (bug #1) |
| M2 no directory→marker enforcement / unclear missing-data failure | **Fixed** (bug #2) |
| L1 `.gitignore` gaps (`.env.*`, `*.parquet`, editor dirs) | **Fixed** |
| L2 no enforcement of "no file > 1 MB" | **Fixed** (bug #3); `uv.lock` exempt, logged in DECISIONS.md |
| L3 `check.py` cwd/flag weaknesses | **Fixed** (bug #3) |
| L4 CI not `--locked`, no `permissions`, duplicate triggers | **Fixed**; the unverifiable part (CI never ran, no remote) stays **open until the user pushes** |
| L5 hatchling floor, mypy `warn_return_any`, `--strict-config` | **Fixed**. `[project.scripts]` target `zipcator.cli` is created in phase 4 |
| L6 non-conventional initial commit messages; no `.gitattributes` | History not rewritten (CLAUDE.md); `.gitattributes` added; convention followed from here on |

Not verified: GitHub Actions itself (no remote; the user pushes), Linux/macOS (Windows
3.11 + 3.12 verified locally by the reviewer).

Gates after fixes: ruff lint PASS · ruff format PASS · mypy PASS · pytest PASS
(12 passed, coverage 100%) · tracked-file size PASS.

### Phase 1 — `geo` + `search` on synthetic data (tag `phase-1`)
Reviewer verdict: *must not close until MEDIUM-1/2/3 fixed or deferred*; 0 HIGH. The reviewer
found no wrong results: 300 random-polygon cases at random centers (lat −60..70, radius
0.5–100 mi) agreed with an independent planar disc to ≤ 0.006 pp; a 25,600-cell grid at
100 mi summed to πr² within 0.01 %. It also ran 24 mutants (18 killed).

| Finding | Triage |
|---|---|
| M1 prefilter untested (bypass / naive degree bbox survived) | **Fixed** (bug #5); both mutants re-verified dead |
| M2 sort test can't distinguish nearest-edge vs rep-point key | **Fixed** (bug #6) |
| M3 int `zip` column silently loses leading zeros | **Fixed** (bug #4) |
| L1 tolerances (0.5 pp) far looser than accuracy (0.002 pp) | **Fixed**: 0.05 pp / rel 0.002 / 1e-6; area asserted at AK/HI/PR |
| L2 undocumented-behaviour tests (shared boundary, hole, index, nulls, dtypes, ±180, near-pole) | **Fixed** (`test_search_edge_cases.py`) |
| L3 thin hypothesis test | **Fixed**: added random-center + `min_overlap` partition property. Convex-polygon / MultiPolygon generators **deferred** (explicit MultiPolygon + analytic tests exist) |
| L3 AEQD "exact" claim overstated; `nearest_in_laea` mutant survives | Wording corrected in DECISIONS.md. Mutant **deferred**: the two CRSs agree to ~1e-4 relative, so no meaningful test can separate them |
| L4 tautological circle test | **Fixed** (independent πr² + geographiclib vertex check). Sort-rounding boundary straddle (two distances 1e-9 apart across a 1e-6 rounding edge sort by value, not ZIP) **deferred**: harmless, order stays deterministic |
| L5 `crs=None`, duplicate ZIPs | **Fixed** (bug #4) |
| L5 non-`geometry` geometry column name; non-integral float `population`; CRS84 / non-4326 rebuilds sindex per call; collinear "polygon" reports 100 % | **Deferred to phase 2**: `load_zctas` guarantees the schema (4326, `geometry`, string ZIPs, `Int64` population) so `find_zips` never sees these in the app. *Correction (phase 2 QA bug 9): this entry originally also promised "valid non-degenerate geometry". The loader did not check that. It now checks representative points, state codes and population, but deliberately not geometry validity; see DECISIONS.md.* |
| L6 `total_est_population` understates with partial data | **Fixed**: `SearchResult.population_complete` |
| L6 `AntimeridianError` also covers poles | **Deferred**: cosmetic; name documented in DECISIONS.md |
| Performance: 361 ms at 100 mi / 3,112 hits on a synthetic grid; no test for the "< 50 ms prefilter" claim | **Deferred to phase 6** benchmark (`slow`) on the real artifact (SPEC §6 budget is 1 s) |

Gates after fixes: ruff lint PASS · ruff format PASS · mypy PASS · pytest PASS (130 passed,
coverage 100 % on geo/models/search) · tracked-file size PASS.

### Phase 2 — data build, loader and real artifact (tag `phase-2`)
Reviewer verdict: *must not close until* the simplification overlap/gap effect, the silent ZCTA
drops and the loader promise were resolved; 0 HIGH.

**Verified independently by the reviewer (no zipcator code on the verification side):**
33,791 ZCTAs in D1 = D2 = artifact (none dropped); primary county and state re-derived for all
33,791 from the raw relationship file with the `csv` module: **0 mismatches**, including all 137
multi-state ZCTAs; cities vs the raw GeoNames files: 0 mismatches, 0 blanks; `land_area_sq_mi`
equals ALAND20 exactly; geometry part counts unchanged (no island lost); representative points
inside polygons; accents round-trip; a rebuild from the cached raw files was byte-identical
(reproducible); 543 searches over 23 named and 120 random scenarios (0.5–100 mi) gave worst
drift 0.37 pp on the adaptive build. The facts in DECISIONS.md (903 blank rows, 149 territory
blanks, validate-then-replace, atomic writes) were all checked accurate.

| Finding | Triage |
|---|---|
| M1 per-polygon simplification opens 9,198 km² of neighbour overlap; docs said "topology-preserving" | **Fixed** (bug 7) with coverage simplification. Study redone (table in DECISIONS.md): overlap 0 km², worst drift 0.128 pp (adaptive build: 0.24 pp), 40.2 MB. DECISIONS history corrected |
| M2 "every row has a state" vacuous: rows dropped before validation | **Fixed** (bug 8). The real-data test compares against the raw file's own ZIP list |
| M3 loader thinner than `find_zips` assumes; phase 1 QA claim overstated | **Fixed** (bug 9). Validity deliberately not re-checked on load (cost vs budget); earlier claim corrected |
| L corrupt/empty cached raw file gives a traceback, no `--refresh` hint | **Fixed** (bug 10) |
| L Census key reachable through the exception chain | **Fixed** (bug 10) |
| L cache trusts the file name, not the URL; the real raw files lack sidecars | **Fixed** for URL changes. The three raw files in this checkout have no sidecars, so their timestamps are file mtimes, now flagged `file_mtime` in `metadata.json`. Content hashing **deferred** (cost/benefit) |
| L state label is the primary county's state (13 ZCTAs disagree with USPS) | **Deferred, documented** (DECISIONS.md); README limitation in phase 6 |
| L `land_area_sq_mi` vs polygon area differ for 788 ZCTAs | **Deferred to phase 5 (UI)**: documented; the UI shows only polygon areas |
| L weak tests: always-true vintage assertion, meaningless stdout assertion, atomicity untested, no straddling-ZCTA / mid-stream / tie-break tests, drift claim only in a script | **Fixed**. Drift, overlap and area are now `data` tests that run every time |
| L config `~` expansion; WARNING marking in the build script; blank-city fill-rate check; empty GeoNames file | **Fixed** |
| L `data_dir()` default wrong for non-editable installs; two-step parquet/metadata replace | **Deferred, documented** (DECISIONS.md); the README documents the checkout install and `ZIPCATOR_DATA_DIR` |
| D4 population never run against the live API | **Open**: needs a Census key; mocked request/response shapes only. Population stays optional |

Gates after fixes: ruff lint PASS · ruff format PASS · mypy PASS · pytest PASS (254 passed,
99 % coverage, data_build 100 %) · `pytest -m data` 23 passed (~30 s) · tracked-file size PASS.
Artifact: 33,791 ZCTAs, 40.2 MB, loads in ~0.5 s.
