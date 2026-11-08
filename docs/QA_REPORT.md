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
| L5 non-`geometry` geometry column name; non-integral float `population`; CRS84 / non-4326 rebuilds sindex per call; collinear "polygon" reports 100 % | **Deferred to phase 2**: `load_zctas` guarantees the schema (4326, `geometry`, string ZIPs, `Int64` population, valid non-degenerate geometry) so `find_zips` never sees these in the app |
| L6 `total_est_population` understates with partial data | **Fixed**: `SearchResult.population_complete` |
| L6 `AntimeridianError` also covers poles | **Deferred**: cosmetic; name documented in DECISIONS.md |
| Performance: 361 ms at 100 mi / 3,112 hits on a synthetic grid; no test for the "< 50 ms prefilter" claim | **Deferred to phase 6** benchmark (`slow`) on the real artifact (SPEC §6 budget is 1 s) |

Gates after fixes: ruff lint PASS · ruff format PASS · mypy PASS · pytest PASS (130 passed,
coverage 100 % on geo/models/search) · tracked-file size PASS.
