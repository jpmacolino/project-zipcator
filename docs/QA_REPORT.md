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
| 11 | 3 | (test gap) a rate limiter that stopped updating its timestamp throttled only the 2nd request and let later ones burst, with all 358 tests green | No test made ≥ 3 back-to-back Nominatim calls | Tests assert every call is ≥ 1 s apart (`sleeps == [1,1,1,1]`) | `test_limiter_spaces_every_call_not_just_the_second`, `test_four_back_to_back_nominatim_requests_are_each_a_second_apart` (mutant killed) |
| 12 | 3 | "Location is outside the US" (SPEC §5.2.5) could never be produced: Census is US-only, Nominatim is filtered, and coordinates anywhere were accepted (Sydney, a swapped lat/lon) | The error existed only for a doctored mock | Coarse US/territory boxes checked in `locate()` for coordinates and geocoder results | `test_non_us_coordinates_raise_outside_the_us`, `test_a_geocoder_result_outside_the_us_is_rejected`, `test_swapped_census_coordinates_are_caught_downstream`, `test_us_and_territory_coordinates_are_accepted` |
| 13 | 3 | `parse_input("٣٣٦٠٢")` / fullwidth digits became a non-ASCII "ZIP" that never matches a ZCTA (misleading "not a known ZIP") | `\d` matches Unicode digits | `[0-9]` regex + NFKC normalisation | `test_arabic_indic_digits_are_not_a_zip`, `test_fullwidth_digits_are_normalised_to_a_real_zip`, `test_near_miss_zips_are_not_zips` (`\d{4,5}` mutant killed) |
| 14 | 3 | `CachedGeocoder` returned a shared mutable `candidates` list (a caller could poison later hits) and crashed with `KeyError` on a negative `maxsize`; removing the cache from `default_geocoder()` went unnoticed | No copy on read; no validation; no wiring test | Copy in/out, `maxsize` validated (0 disables), wiring test | `test_cached_results_cannot_be_poisoned_by_callers`, `test_negative_cache_size_is_rejected`, `test_default_geocoder_really_caches` |
| 15 | 4 | `zipcator ... --zips --csv f` printed "Wrote N row(s)" on stdout, so a piped ZIP list was corrupted (help promised only the list) | Status message printed unconditionally to stdout | In `--zips` mode all non-list output goes to stderr | `test_zips_with_csv_keeps_stdout_to_the_list_only`, `test_zips_with_csv_pipes_nothing_but_the_list_on_stdout` (real process) |
| 16 | 4 | `UnicodeEncodeError` traceback when stdout/stderr is redirected on Windows (cp1252) and the label or `--csv` path has characters outside it (CJK, emoji, macrons); CSV lost or exit code hidden behind 1; CRLF in piped output | Default stream encoding/newline, no error handler | Streams reconfigured: `errors="replace"`, LF when not a TTY | `test_streams_are_hardened_so_unencodable_text_cannot_crash_printing`, `test_piped_output_uses_unix_newlines`, `test_cp1252_redirected_output_with_a_cjk_csv_path_does_not_crash` (real process) |
| 17 | 4 | Display showed `0.0 % / 0.00 sq mi` for a ZIP that is listed (real: Tampa 10 mi, Mayagüez 5 mi) and `100.0` for 2 partially covered NYC ZIPs | Plain 1-dp rounding of the extremes | `display_overlap` floor 0.1 / cap 99.9, `display_area` floor 0.01; CSV exact at 6 dp | `test_display_overlap_never_shows_zero_or_full_for_a_partial_zip`, `test_display_area_never_shows_zero_for_a_listed_zip`, `test_a_tiny_sliver_zip_is_never_shown_as_zero_percent_on_screen` |
| 18 | 4 | `| head` gave a BrokenPipe traceback; Ctrl-C and unexpected exceptions leaked tracebacks; `zipcator "-14.27,-170.7"` said "address is required"; a bad address paid the data load; `--csv ""` was ignored; `-v` was ignored on a second call (`basicConfig`) | Narrow exception handling; argparse treats the token as an option; late validation; `basicConfig` is one-shot and global | BrokenPipe → quiet exit 0 (CSV written first); 130 on Ctrl-C; one-line unexpected-error message (traceback with `-v`); hint about `--`; parse before load; scoped logging handler; empty `--csv` is a usage error | `test_broken_pipe_is_a_quiet_success_not_a_traceback`, `test_closed_pipe_is_quiet_and_still_writes_the_csv` (real process), `test_ctrl_c_exits_130_quietly`, `test_unexpected_exception_is_a_clean_error_without_a_traceback`, `test_negative_first_coordinate_without_a_space_gets_a_helpful_hint`, `test_a_bad_address_fails_before_the_data_is_loaded`, `test_verbose_logs_progress_to_stderr_and_default_stays_quiet` |
| 19 | 4 | The plain CSV had a UTF-8 BOM, so `csv.reader(open(f, encoding="utf-8"))` returned the header `'﻿"ZIP"'`; overwriting an existing CSV could truncate it on failure | BOM on every CSV; direct `write_bytes` | BOM only on the Excel-safe file; atomic temp-file + replace | `test_cli_plain_csv_has_no_bom_and_reads_with_plain_utf8`, `test_failed_csv_replace_keeps_the_previous_file_and_cleans_up` |
| 20 | 5 | Hovering inside the radius circle never showed the ZIP / city / overlap tooltip, only "N-mile radius" (SPEC §5.4 tooltip effectively missing); AppTest and the first visual tests could not see it | The filled circle was added above the ZIP layer and was interactive; folium drops unknown options (`interactive=`); Leaflet's CSS outranks a plain class rule | Circle gets `className` + `path.zipcator-radius{pointer-events:none !important}`; radius shown in the legend | `test_zip_tooltips_work_inside_the_radius_circle` (real browser, 25 hover points), `test_the_radius_circle_does_not_capture_the_mouse_so_zip_tooltips_work` |
| 21 | 5 | A backtick in the matched address blanked the map ("missing ) after argument list") and `${…}` would execute | folium puts the marker tooltip in a JS template literal; `html.escape` leaves backtick, `$`, backslash | `mapview.js_safe` (HTML character references) | `test_marker_label_cannot_break_out_of_the_javascript_template`, `test_hostile_map_label_does_not_break_the_map_or_run_script` (real browser) |
| 22 | 5 | An address containing markdown rendered as bold/links/an image the browser fetched | Geocoder text interpolated into `st.markdown`/`st.caption` | `ui.markdown_safe` for geocoder labels; ZIP/coordinate labels (ours) untouched | `test_markdown_safe_neutralises_every_markup_character`, `test_hostile_address_text_renders_as_literal_text_not_markup` (real browser), `test_a_hostile_matched_address_is_shown_as_text_and_cannot_break_the_map` |
| 23 | 5 | (test gap) removing `html.escape` on city names survived all tests | The old assertion looked at the page source, where JSON encoding already hides `<>&` | Assert the decoded GeoJSON property | `test_city_names_are_html_escaped_in_the_geojson_property_itself` |
| 24 | 5 | Found by looking at the screenshots: controls hidden in a collapsed sidebar on phones; an oversized branca legend covering the map and labels; sliders showing raw floats ("5.00"); table numbers without fixed decimals; the summary as an enormous heading; barely visible hidden-ZIP outlines; the Deploy button | First-draft layout; branca legend is a large SVG | Controls in the page, compact HTML legend, `format=` on sliders and per-column table formats, bold summary line, dashed 2 px grey outlines, committed `.streamlit/config.toml` | `test_phone_width_with_results_fits_without_horizontal_scroll`, `_assert_map` (legend text, one polygon per ZIP, one marker), `test_min_overlap_with_grey_outlines`, `test_legend_is_present_and_uses_the_colour_scale` |
| 25 | 5 | Footer claimed an OpenStreetMap Nominatim fallback while it was disabled; the checkbox label truncated at ~1000 px | Static footer text; long label | `footer_markdown(nominatim_enabled)` follows `ZIPCATOR_DISABLE_NOMINATIM`; shorter label | `test_footer_does_not_claim_a_nominatim_fallback_when_it_is_disabled`, `test_footer_follows_the_environment_by_default` |

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

### Phase 3 — geocoding and input parsing (tag `phase-3`)
Reviewer verdict: *must not close until* 5 items were done; 0 HIGH. The reviewer found no wrong
results from the shipped behaviour and ran **66 single-line mutants** of `geocode.py`: 53 killed,
2 patterns missing, 11 survived; the spec §8.2 mutants were killed (limiter removed, shared
interval, `countrycodes` dropped, User-Agent dropped, x/y swap, retry count/backoff, timeout,
US filter, chain conditions, env disable, cache casefold/LRU, coordinate bounds). It confirmed the
live tests are opt-in (`pytest` and `pytest -m "not slow"` both skip them; 4 live tests are
skipped, not run) and polite. Live run (manual, once): **4 passed**: 3 Census requests, 1
Nominatim request, all matching the parser built from recorded fixtures.

| Finding | Triage |
|---|---|
| M1 no test with ≥ 3 Nominatim calls: a limiter that stops updating `_last` survived | **Fixed** (bug 11); mutant re-run and killed |
| M2 "Location is outside the US" unreachable in any real flow | **Fixed** (bug 12): coarse US/territory boxes in `locate()`, DECISIONS.md records the border-town limitation |
| M3 `\d` accepts non-ASCII digits as ZIPs | **Fixed** (bug 13): `[0-9]` + NFKC; the `\d{4,5}` mutant is killed |
| L cache: negative `maxsize` crashes, shared mutable `candidates`, no default-cache test | **Fixed** (bug 14); stampede protection **deferred** (limiter serialises; documented) |
| L no input length cap (3,000-char input read as an outage); degree notation silently an address | **Fixed**: 300-char cap with a clear message; degree-symbol hint. `N/S/E/W` letters deliberately not detected (would break `123 N Main St`) |
| L `Retry-After` ignored; `retries=-1` makes no request; invalid User-Agent retried as an outage | **Fixed** |
| L Census coordinates not plausibility-checked (swap, Paris) | **Fixed** for the swap/foreign cases via the US-box check; plausibility against the returned ZIP **deferred** |
| L parsing quirks: `-0`, `None` input, unknown-ZIP wording ("00501 is not a ZIP") | **Fixed**; newline-separated numbers and `12 34` remain "two numbers = coordinates" by design (documented) |
| L surviving mutants: 500 transient, JSON body on 4xx, uppercase `US`, chain on invalid input, `limit`, candidate order, `;` separator, explicit/late-bound User-Agent | **Fixed**: tests added; 500-transient mutant re-run and killed |
| L design choices not logged in DECISIONS.md | **Fixed** |
| L 10 s is a per-request timeout, not a total deadline; no response-size cap | **Deferred, documented** (worst case ≈ 40 s/service; acceptable for v1) |
| `default_geocoder()` builds a new cache per call | **Carried to phase 5**: the app must hold one instance |

Gates after fixes: ruff lint PASS · ruff format PASS · mypy PASS · pytest PASS (434 passed,
99 % coverage, geocode.py 100 % line and branch) · tracked-file size PASS · `pytest -m network`
4 passed (manual).

### Phase 4 — export and CLI (tag `phase-4`)
Reviewer verdict: *must not close until* four items were done; 0 HIGH. It ran the real CLI many times
on the real data (2 Census requests, 0 Nominatim). Its mutation run: **79 of 92 killed**; the
survivors were mostly untested behaviours (a vacuous `-v` test, only `City` defusing tested,
min-overlap bounds, summary clauses, decimals, empty-result output). It also found that
**coverage proved little** here (99.85 % while many behaviours were never asserted).
Verified by the reviewer: bounds (`0.5`, `100`, `1e1` accepted; `0.49`, `100.0001`, `nan`, `-0` rejected),
locked file / directory / non-ASCII and spaced paths give clean exit 1, a 100 mi NYC search takes
~0.9 s of work, Census returning HTML/`null`/lists/NaN coordinates/HTTP 500 all give a clean exit 1.

After the fixes I re-ran 21 targeted mutants (quote style, BOMs, decimals, defusing, de-dup,
separators, display cap/floor, min-overlap and radius bounds, stdout/stderr routing, exit codes,
parse-before-load, table index, atomic write): **19 were killed outright**. The other 2
("defuse drops CR", "zip_list swaps separators") were reported as survivors only because my sed
patterns containing `\n`/`\t` never matched and my no-op check was unreliable (the files already had
uncommitted changes). Re-run with exact-string replacement, those two plus three more
defusing/separator variants (5 mutants) were all **killed**. This is a sample of the behaviours the
reviewer flagged, not a full mutation run.

| Finding | Triage |
|---|---|
| M1 `--zips --csv` wrote "Wrote N row(s)" to stdout | **Fixed** (bug 15); real-process test |
| M2 `UnicodeEncodeError` traceback on redirected cp1252 stdout/stderr | **Fixed** (bug 16); real-process test with a CJK `--csv` path under `PYTHONIOENCODING=cp1252` |
| M3 default CSV does not open correctly in Excel | **Decided and logged** (bug 19 / DECISIONS.md): two variants, Excel-first in the app, plain default in the CLI. **Open: verify in real Excel** (not possible here) |
| M4 vacuous `-v` test + surviving mutants | **Fixed**: `-v` now logs real progress and is tested; tests for min-overlap bounds/default, summary clauses and rounding, table/CSV decimals, every free-text column defused, empty-result output |
| L BOM + quoted header breaks `csv.reader` under plain utf-8 | **Fixed** (bug 19): BOM only on the Excel-safe file |
| L defusing prefix and "corrupting legitimate data" | **Accepted, documented** (0 real values affected); all three text columns now tested |
| L misleading display at extremes (`0.0 %` listed ZIP, partial ZIP shown `100.0`) | **Fixed** (bug 17): floor 0.1 / cap 99.9 / area floor 0.01 on screen; CSV exact (6 dp) |
| L BrokenPipe, Ctrl-C, unexpected exceptions leak tracebacks | **Fixed** (bug 18) |
| L negative first coordinate gives "address is required" | **Fixed** (bug 18): hint about `--` (options go before it) |
| L `logging.basicConfig` ignores `-v` on a repeat call and mutates the root logger | **Fixed** (bug 18): handler scoped to each call |
| L bad address pays the data-load cost; `--csv ""` silently ignored | **Fixed** (bug 18) |
| L CRLF in piped `--zips --newline` output; `-0.0`; non-atomic CSV write; dead `EXIT_USAGE` | **Fixed** |
| L CSV omits `intpt_*` / `land_area_sq_mi` ("all columns") | **Decided, documented** (DECISIONS.md) |
| L population columns never exercised on real data | **Open**: default artifact has no population (needs a Census key); synthetic tests cover them |
| Test isolation: `scripts/build_data.py`'s `basicConfig(force=True)` left a closed-stream root handler that broke a later test | **Fixed in the test suite** (autouse fixture restores root logging) |
| Own slip: a sed expanded `\xef\xbb\xbf` into a literal BOM inside two bytes literals (syntax error), committed in one commit | **Fixed** in the next commit (history not rewritten) |

Gates after fixes: ruff lint PASS · ruff format PASS · mypy PASS · pytest PASS (602 passed, 99 %
coverage, `cli.py` and `export.py` 100 %) · `pytest -m data` 34 passed, 1 skipped (the live CLI test, by
design) · tracked-file size PASS. Live: `pytest -m network` 5 passed (manual, once).

### Phase 5 — Streamlit app and map (tag `phase-5`)
Reviewer verdict: *must not close until* the circle/tooltip defect, the injection issues and the
`html.escape` test gap were fixed; 1 HIGH, 3 MEDIUM. The reviewer drove the **real app in Chromium**
(ports 8611–8614, 2 Census requests, 0 Nominatim) and ran a 43-mutant harness on a temp copy (38 killed
or invalid). Verified OK there: both downloads (BOM, `="02108"`, filenames, 92 rows), Enter-to-submit,
number/slider sync incl. rapid drags (0 exceptions), zoom/pan/click = 0 websocket frames, two
simultaneous sessions without leakage, the real ambiguous address "100 Main St, Springfield" (7
options, picker re-centres), missing and corrupt data messages, 100 mi performance (table in
DECISIONS.md). The review was interrupted once by a usage limit and resumed from its saved state.

| Finding | Triage |
|---|---|
| H1 ZIP tooltips unreachable inside the circle (circle above the ZIP layer took every hover) | **Fixed** (bug 20). My first fix (`interactive=False`) was silently dropped by folium; the second (a CSS class) was overridden by Leaflet's CSS; the real-browser hover test caught both, then passed |
| M2 matched address injected into folium's JS template literal (backtick blanks the map, `${…}` runs) | **Fixed** (bug 21): `js_safe`; verified in a real browser with `${window.__pwned=2}` |
| M3 markdown injection in the summary and "Center:" lines (bold/links/an image the browser fetched) | **Fixed** (bug 22): `markdown_safe`. A bare URL still auto-links with honest text: asserted harmless, documented |
| M4 removing `html.escape` on city names survived (JSON encoding hid it) | **Fixed** (bug 23): asserts the decoded property value |
| L surviving mutants: fit-bounds order, coordinate grid, legend, tooltip clamp, fill opacity, home outline colour, CSV filenames | **Fixed**: 18/18 targeted mutants killed. Filename/`token` mutants: filenames are asserted in a real-browser download test; the cache `token` is **deferred** (a rebuild needs a server restart anyway, so stale-frame risk is nil) |
| L visual tests fragile / vacuous (two "Search" buttons once a table exists; legend assertion satisfied by the zoom control; no automated §9.5 check) | **Fixed**: form-scoped selector, legend text assertion, circle width vs scale bar in 3 scenarios |
| L UX: sliders live while address is stale; emptied number box → 0.5; off-step radius accepted; truncated checkbox label; footer claimed Nominatim when disabled | Checkbox label and footer **fixed**; the rest **accepted and documented** |
| L map rebuilt on every rerun (0.6 s NYC) | **Deferred** (within budget) |
| L no phase 5 docs / QA rows | **Fixed** (this entry; bugs 20–25) |

Gates after fixes: ruff lint PASS · ruff format PASS · mypy PASS · pytest PASS (699 passed, 99 %
coverage; `mapview`/`ui` 100 %) · `pytest -m visual` 11 passed (real Chromium) · `pytest -m data`
passes · tracked-file size PASS.
