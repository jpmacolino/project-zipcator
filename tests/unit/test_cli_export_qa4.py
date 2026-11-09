"""Regression tests for the phase 4 QA review (docs/QA_REPORT.md, phase 4)."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pandas as pd
import pytest
from shapely.geometry import box
from zc_testkit.synth import MILE_M, TAMPA, make_gdf

from zipcator import cli, export
from zipcator.models import GeocodeCandidate, GeocodeResult
from zipcator.search import find_zips

ADDRESS = "401 E Jackson St, Tampa, FL 33602"
R10 = 10 * MILE_M


def _zctas():  # type: ignore[no-untyped-def]
    return make_gdf(
        {
            "33602": box(-1000, -1000, 1000, 1000),
            "33603": box(3000, 3000, 6000, 6000),
            "02108": box(0, 14_000, 20_000, 34_000),
        },
        population={"33602": 3500, "33603": 600, "02108": 10_000},
    )


class _Geo:
    def __init__(self, label: str = "401 JACKSON ST, TAMPA, FL, 33602") -> None:
        self.label = label

    def geocode(self, query: str) -> GeocodeResult:
        c = GeocodeCandidate(TAMPA[0], TAMPA[1], self.label, "census")
        return GeocodeResult(TAMPA[0], TAMPA[1], self.label, "census", [c])


class _Boom:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def geocode(self, query: str) -> GeocodeResult:
        raise self.exc


def run(args: list[str], capsys: pytest.CaptureFixture[str], geocoder: object | None = None):
    code = cli.main(args, geocoder=geocoder or _Geo(), zctas=_zctas())  # type: ignore[arg-type]
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# ------------------------------------------------------------- --zips keeps stdout clean
def test_zips_with_csv_keeps_stdout_to_the_list_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: 'Wrote N row(s)' was printed to stdout, corrupting piped ZIP lists."""
    target = tmp_path / "z.csv"
    code, out, err = run([ADDRESS, "--zips", "--csv", str(target)], capsys)
    assert code == 0 and target.exists()
    assert out.strip() == "33602, 33603, 02108"
    assert "Wrote 3 row(s)" in err and "3 ZIP codes overlap" in err


def test_table_mode_reports_the_csv_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run([ADDRESS, "--csv", str(tmp_path / "t.csv")], capsys)
    assert code == 0 and "Wrote 3 row(s)" in out and err == ""


def test_csv_is_written_even_if_stdout_is_closed_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`zipcator ... --csv f | head -1` must still produce the file."""
    target = tmp_path / "pipe.csv"

    def broken_print(*_a: object, **_k: object) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(cli, "print", broken_print, raising=False)
    code = cli.main([ADDRESS, "--csv", str(target)], geocoder=_Geo(), zctas=_zctas())
    assert code == 0 and target.exists()  # quiet exit, nothing lost
    capsys.readouterr()


def test_broken_pipe_is_a_quiet_success_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken_print(*_a: object, **_k: object) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(cli, "print", broken_print, raising=False)
    assert cli.main([ADDRESS], geocoder=_Geo(), zctas=_zctas()) == 0
    assert "Traceback" not in capsys.readouterr().err


# ---------------------------------------------------- Windows console / redirected encodings
def test_streams_are_hardened_so_unencodable_text_cannot_crash_printing() -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", newline="\r\n")  # a redirected Windows stdout
    cli._harden_stream(stream)
    print("Calle Ñandú, 東京 😀, Mayagüez", file=stream, flush=True)  # would raise before
    out = raw.getvalue().decode("cp1252")
    assert "Calle Ñandú" in out and "Mayagüez" in out  # encodable text is untouched
    assert "?" in out  # the rest degraded instead of crashing


def test_piped_output_uses_unix_newlines() -> None:
    """A piped --zips --newline list must not be CRLF (breaks `while read` loops)."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="utf-8", newline="\r\n")
    cli._harden_stream(stream)
    print("a\nb", file=stream, flush=True)
    assert raw.getvalue() == b"a\nb\n"


def test_harden_stream_ignores_objects_that_cannot_be_reconfigured() -> None:
    cli._harden_stream(io.StringIO())  # no .reconfigure: must not raise
    cli._harden_stream(None)  # type: ignore[arg-type]  # e.g. pythonw has no stdout


def test_non_cp1252_geocoder_label_does_not_crash_main(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run([ADDRESS], capsys, _Geo("Calle Ñandú, 東京 😀, Mayagüez"))
    assert code == 0 and "Mayagüez" in out


# ------------------------------------------------------------------- exceptions / signals
def test_unexpected_exception_is_a_clean_error_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = run([ADDRESS], capsys, _Boom(RuntimeError("kaboom")))
    assert code == 1 and out == ""
    assert "unexpected" in err.lower() and "kaboom" in err and "Traceback" not in err
    assert "-v" in err  # tells the user how to get details


def test_unexpected_exception_shows_the_traceback_with_verbose(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run([ADDRESS, "-v"], capsys, _Boom(RuntimeError("kaboom")))
    assert code == 1 and "Traceback" in err


def test_ctrl_c_exits_130_quietly(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run([ADDRESS], capsys, _Boom(KeyboardInterrupt()))
    assert code == 130 and "Traceback" not in err and "Interrupted" in err


# ---------------------------------------------------------------------- argument handling
def test_negative_first_coordinate_without_a_space_gets_a_helpful_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`zipcator "-14.27,-170.7"` looked like an unknown option: 'address is required'."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["-14.27,-170.7"], geocoder=_Geo(), zctas=_zctas())
    err = capsys.readouterr().err
    assert exc.value.code == 2 and "--" in err and "negative" in err.lower()


def test_double_dash_lets_a_negative_coordinate_through(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["--", "-14.27,-170.7"], capsys)
    assert code == 0 and "-14.27, -170.7" in out


def test_empty_csv_path_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([ADDRESS, "--csv", ""], geocoder=_Geo(), zctas=_zctas())
    assert exc.value.code == 2 and "--csv" in capsys.readouterr().err


def test_a_bad_address_fails_before_the_data_is_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invalid input must not pay the ~0.5 s data load (here: the data does not even exist)."""
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path / "nope"))
    code = cli.main(["95, 10"], geocoder=_Geo())
    err = capsys.readouterr().err
    assert code == 1 and "latitude" in err and "build_data" not in err


@pytest.mark.parametrize("pct", ["0", "100", "0.0", "100.0"])
def test_min_overlap_bounds_are_inclusive(pct: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = run([ADDRESS, "--min-overlap", pct], capsys)
    assert code == 0


def test_min_overlap_100_keeps_only_fully_covered_zips_and_the_home_zip(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _ = run([ADDRESS, "-m", "100"], capsys)
    assert "33602" in out and "33603" in out and "02108" not in out


def test_default_min_overlap_shows_every_overlapping_zip(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _ = run([ADDRESS], capsys)
    assert "02108" in out and "at least" not in out  # no filter clause when min-overlap is 0


def test_short_flags_work(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run([ADDRESS, "-r", "0.5", "-m", "0"], capsys)
    assert code == 0 and "0.5-mile" in out


def test_table_is_clean_no_index_no_nan_text(capsys: pytest.CaptureFixture[str]) -> None:
    zctas = _zctas()
    zctas.loc[zctas["zip"] == "33603", "city"] = None
    code = cli.main([ADDRESS], geocoder=_Geo(), zctas=zctas)
    out = capsys.readouterr().out
    assert code == 0
    rows = [ln for ln in out.splitlines()[3:] if ln.strip()]  # skip summary, blank, header
    assert rows[0].split()[0] == "33602"  # first token is the ZIP, not a pandas row index
    assert "NaN" not in out and "<NA>" not in out and "None" not in out


def test_empty_result_prints_only_the_summary(capsys: pytest.CaptureFixture[str]) -> None:
    far = make_gdf({"99999": box(60_000, 0, 61_000, 1000)})
    code = cli.main([ADDRESS], geocoder=_Geo(), zctas=far)
    out = capsys.readouterr().out
    assert code == 0 and len(out.strip().splitlines()) == 1


# ---------------------------------------------------------------------- verbose / logging
def test_verbose_logs_progress_to_stderr_and_default_stays_quiet(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, _, quiet = run([ADDRESS], capsys)
    _, _, loud = run([ADDRESS, "-v"], capsys)  # second call in the same process
    assert quiet == ""
    assert "loaded" in loud.lower() and "ZIP" in loud


def test_repeated_calls_do_not_stack_log_handlers_or_freeze_the_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run([ADDRESS, "-v"], capsys)
    run([ADDRESS], capsys)
    _, _, err = run([ADDRESS, "-v"], capsys)
    assert err.lower().count("loaded") == 1, err  # one handler, level re-applied each call


# ---------------------------------------------------------------------------- CSV writing
def test_csv_overwrite_is_atomic_and_leaves_no_temp_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "o.csv"
    target.write_text("old contents")
    code, _, _ = run([ADDRESS, "--csv", str(target)], capsys)
    assert code == 0 and "old contents" not in target.read_text(encoding="utf-8")
    assert [p.name for p in tmp_path.iterdir()] == ["o.csv"]


def test_failed_csv_replace_keeps_the_previous_file_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "keep.csv"
    target.write_text("precious")

    def deny(self: Path, _t: object) -> Path:
        raise PermissionError("locked by another program")

    monkeypatch.setattr(Path, "replace", deny)
    code, _, err = run([ADDRESS, "--csv", str(target)], capsys)
    assert code == 1 and "Could not write" in err
    assert target.read_text() == "precious"
    assert [p.name for p in tmp_path.iterdir()] == ["keep.csv"]


def test_cli_plain_csv_has_no_bom_and_reads_with_plain_utf8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "p.csv"
    run([ADDRESS, "--csv", str(target)], capsys)
    raw = target.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    header = next(csv.reader(io.StringIO(raw.decode("utf-8"), newline="")))
    assert header[0] == "ZIP"  # with a BOM this would be '﻿ZIP'


def test_cli_excel_csv_has_a_bom(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "x.csv"
    run([ADDRESS, "--csv", str(target), "--excel"], capsys)
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")


# ====================================================================================== export
def _res():  # type: ignore[no-untyped-def]
    return find_zips(_zctas(), TAMPA, 10)


def test_plain_csv_bytes_have_no_bom_but_the_excel_variant_does() -> None:
    plain, excel = export.csv_bytes(_res()), export.csv_bytes(_res(), excel_safe=True)
    assert not plain.startswith(b"\xef\xbb\xbf")
    assert excel.startswith(b"\xef\xbb\xbf")
    assert plain.decode("utf-8") == export.to_csv(_res())
    assert excel.decode("utf-8-sig") == export.to_csv(_res(), excel_safe=True)


def test_csv_header_order_is_explicit_including_the_zip_area_column() -> None:
    header = next(csv.reader(io.StringIO(export.to_csv(_res()), newline="")))
    assert header == [
        "ZIP",
        "City",
        "County",
        "State",
        "Overlap %",
        "Area in radius (sq mi)",
        "ZIP area (sq mi)",
        "Nearest distance (mi)",
        "Center distance (mi)",
        "Contains address",
        "Population",
        "Est. pop in radius",
    ]


@pytest.mark.parametrize("column", ["city", "county", "state"])
@pytest.mark.parametrize("trigger", ["=1+1", "+1", "-1", "@x", "\tx", "\rx"])
def test_every_free_text_column_is_defused(column: str, trigger: str) -> None:
    gdf = make_gdf({"02108": box(-1000, -1000, 1000, 1000)})
    gdf[column] = trigger
    rows = list(csv.reader(io.StringIO(export.to_csv(find_zips(gdf, TAMPA, 10)), newline="")))
    cell = rows[1][rows[0].index(column.capitalize())]
    assert cell == "'" + trigger


def test_defuse_leaves_legitimate_text_alone() -> None:
    for text in ["Winston-Salem", "O'Fallon", "St. Louis", "A=B Corp", "x@y"]:
        assert export.defuse(text) == text  # only a *leading* trigger is touched


def test_csv_numbers_keep_six_decimals_so_tiny_overlaps_are_not_zero() -> None:
    rows = list(csv.reader(io.StringIO(export.to_csv(_res()), newline="")))
    header = rows[0]
    col = header.index("Area in radius (sq mi)")
    decimals = [len(r[col].split(".")[1]) for r in rows[1:] if "." in r[col] and "e" not in r[col]]
    assert max(decimals) == 6  # kills 4-dp / 2-dp regressions


def test_display_table_uses_two_decimals_for_areas_and_distances() -> None:
    df = export.result_table(_res())
    for col in ("Area in radius (sq mi)", "Nearest distance (mi)", "Center distance (mi)"):
        assert (df[col] == df[col].round(2)).all()
    assert (df["Area in radius (sq mi)"] != df["Area in radius (sq mi)"].round(1)).any()


def test_csv_overlap_matches_the_table_value_exactly() -> None:
    res = _res()
    rows = list(csv.reader(io.StringIO(export.to_csv(res), newline="")))
    got = [float(r[rows[0].index("Overlap %")]) for r in rows[1:]]
    assert got == [round(v, 6) for v in res.matches["overlap_pct"]]


def test_missing_values_are_blank_in_the_csv_not_nan() -> None:
    gdf = make_gdf({"02108": box(-1000, -1000, 1000, 1000)}).drop(columns=["city"])
    text = export.to_csv(find_zips(gdf, TAMPA, 10))
    rows = list(csv.reader(io.StringIO(text, newline="")))
    assert rows[1][1] == "" and "nan" not in text.lower() and "<NA>" not in text


def test_negative_zero_never_appears() -> None:
    res = _res()
    res.matches.loc[0, "nearest_distance_mi"] = -0.0
    res.matches.loc[0, "center_distance_mi"] = -0.0
    assert "-0.0" not in export.to_csv(res).replace("-0.0000", "")  # no '-0.0...' cell
    assert (export.result_table(res)["Nearest distance (mi)"].astype(str) != "-0.0").all()
    assert "-0.0" not in export.result_table(res).to_string()


# ----------------------------------------------------- misleading display rounding (extremes)
@pytest.mark.parametrize(
    ("pct", "shown"),
    [
        (50.04, 50.0),
        (12.3456, 12.3),
        (0.0038, 0.1),  # listed ZIP must never display as 0.0
        (0.04, 0.1),
        (0.05, 0.1),
        (0.14, 0.1),
        (0.16, 0.2),
        (99.96, 99.9),  # a partial ZIP must never display as 100.0
        (99.94, 99.9),
        (99.9999, 99.9),
        (100 - 1e-9, 100.0),  # float noise on a fully covered ZIP
        (99.99999999999999, 100.0),
        (100.0, 100.0),
    ],
)
def test_display_overlap_never_shows_zero_or_full_for_a_partial_zip(
    pct: float, shown: float
) -> None:
    assert export.display_overlap(pct) == shown


def test_display_area_never_shows_zero_for_a_listed_zip() -> None:
    assert export.display_area(0.0004) == 0.01
    assert export.display_area(0.004) == 0.01
    assert export.display_area(0.006) == 0.01
    assert export.display_area(0.0149) == 0.01
    assert export.display_area(12.3456) == 12.35
    assert export.display_area(0.0) == 0.0


def test_a_tiny_sliver_zip_is_never_shown_as_zero_percent_on_screen() -> None:
    """Real case: Tampa 10 mi listed a ZIP at '0.0 % / 0.00 sq mi'."""
    zone = box(-100_000, R10 - 100, 100_000, R10 + 100_000)  # a huge ZIP just clipping the circle
    gdf = make_gdf({"33601": box(-1000, -1000, 1000, 1000), "33699": zone})
    res = find_zips(gdf, TAMPA, 10)
    raw = res.matches.set_index("zip").loc["33699"]
    assert 0 < raw.overlap_pct < 0.05  # the premise: it would round to 0.0
    shown = export.result_table(res).set_index("ZIP").loc["33699"]
    assert shown["Overlap %"] == 0.1  # floor: a listed ZIP is never 0.0
    assert shown["Area in radius (sq mi)"] == max(round(raw.area_in_radius_sq_mi, 2), 0.01)
    assert shown["Area in radius (sq mi)"] > 0
    full = export.result_table(res, full=True).set_index("ZIP").loc["33699"]
    assert 0 < full["Overlap %"] < 0.05  # the CSV keeps the exact (tiny) value
    assert full["Area in radius (sq mi)"] > 0


# ------------------------------------------------------------------------------ summary_line
def test_summary_line_rounds_the_population_to_whole_people() -> None:
    line = export.summary_line(_res(), "x")
    m = re.search(r"about ([\d,]+) people", line)
    assert m is not None  # an unrounded float would contain '.' and not match


def test_summary_line_has_no_filter_clause_at_zero_and_formats_fractions() -> None:
    gdf = _zctas()
    assert "at least" not in export.summary_line(find_zips(gdf, TAMPA, 10), "x")
    low = export.summary_line(find_zips(gdf, TAMPA, 10, min_overlap_pct=0.1), "x")
    assert "at least 0.1% overlap" in low
    whole = export.summary_line(find_zips(gdf, TAMPA, 10, min_overlap_pct=5), "x")
    assert "at least 5% overlap" in whole and "5.0%" not in whole


def test_summary_line_population_total_is_comma_grouped() -> None:
    gdf = make_gdf({"02108": box(-1000, -1000, 1000, 1000)}, population={"02108": 2_000_000_000})
    assert "about 2,000,000,000 people" in export.summary_line(find_zips(gdf, TAMPA, 10), "x")


def test_pandas_dtype_roundtrip_of_the_plain_csv_keeps_zeros_only_when_asked() -> None:
    """Documents the pitfall the Excel variant exists for."""
    text = export.to_csv(find_zips(make_gdf({"02108": box(-1000, -1000, 1000, 1000)}), TAMPA, 10))
    assert pd.read_csv(io.StringIO(text))["ZIP"].tolist() == [2108]  # zeros lost without dtype
    assert pd.read_csv(io.StringIO(text), dtype={"ZIP": str})["ZIP"].tolist() == ["02108"]


def test_harden_stream_on_a_terminal_replaces_errors_but_keeps_native_newlines() -> None:
    class Tty(io.TextIOWrapper):
        def isatty(self) -> bool:
            return True

    raw = io.BytesIO()
    stream = Tty(raw, encoding="cp1252", newline="\r\n")
    cli._harden_stream(stream)
    print("東京\nx", file=stream, flush=True)
    assert raw.getvalue() == b"??\r\nx\r\n"  # replaced, and the console newline untouched


def test_harden_stream_leaves_a_closed_stream_alone() -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    stream.close()
    cli._harden_stream(stream)  # reconfigure raises ValueError on a closed stream: swallowed
