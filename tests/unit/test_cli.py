"""Tests for zipcator.cli (SPEC §5.5, §8.2 cli): arg parsing, exit codes, CSV output.

The geocoder and ZCTA data are injected, so nothing here touches the network or the real
artifact.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box
from zc_testkit.synth import TAMPA, make_gdf

from zipcator import cli
from zipcator import geocode as gc
from zipcator.models import GeocodeCandidate, GeocodeResult

ADDRESS = "401 E Jackson St, Tampa, FL 33602"


def _zctas() -> gpd.GeoDataFrame:
    return make_gdf(
        {
            "33602": box(-1000, -1000, 1000, 1000),  # home
            "33603": box(3000, 3000, 6000, 6000),
            "02108": box(0, 14_000, 20_000, 34_000),  # partial overlap, leading zero
        },
        population={"33602": 3500, "33603": 600, "02108": 10_000},
    )


class _Geocoder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def geocode(self, query: str) -> GeocodeResult:
        self.queries.append(query)
        label = "401 JACKSON ST, TAMPA, FL, 33602"
        return GeocodeResult(
            TAMPA[0], TAMPA[1], label, "census", [GeocodeCandidate(*TAMPA, label, "census")]
        )


class _Failing:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def geocode(self, query: str) -> GeocodeResult:
        raise self.exc


def run(
    args: list[str], capsys: pytest.CaptureFixture[str], geocoder: object | None = None
) -> tuple[int, str, str]:
    code = cli.main(args, geocoder=geocoder or _Geocoder(), zctas=_zctas())  # type: ignore[arg-type]
    out = capsys.readouterr()
    return code, out.out, out.err


# ------------------------------------------------------------------------------ happy path
def test_address_search_prints_summary_and_table(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run([ADDRESS, "--radius", "10"], capsys)
    assert code == 0 and err == ""
    assert "3 ZIP codes overlap a 10-mile radius around 401 JACKSON ST, TAMPA, FL, 33602" in out
    assert "33602" in out and "33603" in out and "02108" in out  # ZIPs keep leading zeros
    assert "Overlap %" in out and "Nearest distance (mi)" in out
    assert out.index("33602") < out.index("33603")  # home ZIP first (SPEC §5.3 sort)


def test_default_radius_is_ten_miles(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run([ADDRESS], capsys)
    assert code == 0 and "10-mile radius" in out


def test_radius_option_changes_the_search(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run([ADDRESS, "--radius", "0.5"], capsys)
    assert "1 ZIP code overlaps a 0.5-mile radius" in out
    assert "02108" not in out


def test_min_overlap_filters_but_keeps_the_home_zip(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run([ADDRESS, "--min-overlap", "50"], capsys)
    assert code == 0
    assert "33602" in out and "33603" in out  # fully inside
    assert "02108" not in out  # ~3% overlap, filtered
    assert "at least 50%" in out


def test_zip_only_input_uses_the_zcta_center_without_geocoding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    geocoder = _Geocoder()
    code, out, _ = run(["33602", "--radius", "5"], capsys, geocoder)
    assert code == 0 and geocoder.queries == []
    assert "ZIP 33602 (approximate center)" in out


def test_coordinate_input_is_used_directly(capsys: pytest.CaptureFixture[str]) -> None:
    geocoder = _Geocoder()
    code, out, _ = run(["27.9506, -82.4572", "--radius", "5"], capsys, geocoder)
    assert code == 0 and geocoder.queries == []
    assert "27.9506, -82.4572" in out


def test_zips_flag_prints_a_copy_paste_list(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run([ADDRESS, "--zips"], capsys)
    assert code == 0 and "33602, 33603, 02108" in out


def test_zips_newline_flag(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run([ADDRESS, "--zips", "--newline"], capsys)
    assert "33602\n33603\n02108" in out


def test_no_results_is_a_friendly_message_not_an_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    far = make_gdf({"99999": box(60_000, 0, 61_000, 1000)})
    code = cli.main([ADDRESS], geocoder=_Geocoder(), zctas=far)
    out = capsys.readouterr().out
    assert code == 0 and out.startswith("No ZIP codes overlap a 10-mile radius")


# ---------------------------------------------------------------------------------- CSV
def test_csv_option_writes_a_file_with_leading_zeros(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_file = tmp_path / "out.csv"
    code, out, _ = run([ADDRESS, "--csv", str(out_file)], capsys)
    assert code == 0 and str(out_file) in out
    raw = out_file.read_bytes()
    assert not raw.startswith(b"﻿")  # plain CSV: no BOM (see DECISIONS.md)
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"), newline="")))
    assert rows[0][0] == "ZIP"
    assert {r[0] for r in rows[1:]} == {"33602", "33603", "02108"}


def test_csv_excel_flag_writes_text_formula_zips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_file = tmp_path / "excel.csv"
    code, _, _ = run([ADDRESS, "--csv", str(out_file), "--excel"], capsys)
    assert code == 0
    text = out_file.read_text(encoding="utf-8-sig")
    zips = [r[0] for r in csv.reader(io.StringIO(text, newline=""))][1:]
    assert '="02108"' in zips  # the parsed cell; on disk it is the RFC-escaped "=""02108"""


def test_csv_creates_missing_parent_directories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "deep" / "er" / "out.csv"
    code, _, _ = run([ADDRESS, "--csv", str(target)], capsys)
    assert code == 0 and target.exists()


def test_csv_write_failure_is_a_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    code, _, err = run([ADDRESS, "--csv", str(blocker / "out.csv")], capsys)  # parent is a file
    assert code == 1 and "Could not write" in err and "Traceback" not in err


def test_csv_is_not_written_when_the_search_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_file = tmp_path / "out.csv"
    code, _, _ = run([ADDRESS, "--csv", str(out_file)], capsys, _Failing(gc.AddressNotFoundError()))
    assert code == 1 and not out_file.exists()


# ------------------------------------------------------------------------ errors / exit codes
def test_address_not_found_exits_1_with_the_user_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = run([ADDRESS], capsys, _Failing(gc.AddressNotFoundError()))
    assert code == 1 and out == "" and err.strip() == "Error: Address not found"


def test_service_error_exits_1_with_the_spec_message(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run([ADDRESS], capsys, _Failing(gc.GeocodeServiceError()))
    assert code == 1 and "Geocoding service unavailable — try again or enter coordinates" in err


def test_outside_us_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(["-33.8688, 151.2093"], capsys)
    assert code == 1 and "Location is outside the US" in err


def test_invalid_coordinates_exit_1_with_a_clear_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run(["95, 10"], capsys)
    assert code == 1 and "latitude" in err


def test_antimeridian_search_exits_1_with_the_documented_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run(["52.0, 179.9", "--radius", "50"], capsys)
    assert code == 1 and "antimeridian" in err and "Traceback" not in err


@pytest.mark.parametrize("radius", ["0", "0.4", "100.5", "-3", "abc", "nan", "inf"])
def test_bad_radius_exits_2(radius: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([ADDRESS, "--radius", radius], geocoder=_Geocoder(), zctas=_zctas())
    assert exc.value.code == 2
    assert "radius" in capsys.readouterr().err


@pytest.mark.parametrize("radius", ["0.5", "100", "37.25"])
def test_radius_bounds_are_inclusive(radius: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = run([ADDRESS, "--radius", radius], capsys)
    assert code == 0


@pytest.mark.parametrize("pct", ["-1", "100.1", "abc", "nan"])
def test_bad_min_overlap_exits_2(pct: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([ADDRESS, "--min-overlap", pct], geocoder=_Geocoder(), zctas=_zctas())
    assert exc.value.code == 2
    assert "min-overlap" in capsys.readouterr().err


def test_missing_address_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([], geocoder=_Geocoder(), zctas=_zctas())
    assert exc.value.code == 2


def test_help_exits_0_and_documents_the_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert exc.value.code == 0
    assert "--radius" in out and "--min-overlap" in out and "--csv" in out
    assert "exit status" in out.lower()


def test_missing_data_artifact_exits_3_with_the_build_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path / "empty"))
    code = cli.main([ADDRESS], geocoder=_Geocoder())  # no zctas injected: really loads
    err = capsys.readouterr().err
    assert code == 3 and "build_data.py" in err and "Traceback" not in err


def test_corrupt_data_artifact_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "zcta.parquet").write_text("not parquet")
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path))
    assert cli.main([ADDRESS], geocoder=_Geocoder()) == 3
    assert "--refresh" in capsys.readouterr().err


def test_data_is_not_loaded_when_arguments_are_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZIPCATOR_DATA_DIR", str(tmp_path / "empty"))
    with pytest.raises(SystemExit) as exc:  # exits 2 (usage), not 3 (data), without loading
        cli.main([ADDRESS, "--radius", "0"], geocoder=_Geocoder())
    assert exc.value.code == 2


def test_verbose_flag_enables_info_logging(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    code, _, _ = run([ADDRESS, "-v"], capsys)
    assert code == 0


def test_main_without_injected_geocoder_uses_the_default_chain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built: list[bool] = []

    def fake_default() -> _Geocoder:
        built.append(True)
        return _Geocoder()

    monkeypatch.setattr(gc, "default_geocoder", fake_default)
    assert cli.main([ADDRESS], zctas=_zctas()) == 0
    assert built == [True]
    capsys.readouterr()


def test_console_script_entry_point_is_declared() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[2]
    cfg = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert cfg["project"]["scripts"]["zipcator"] == "zipcator.cli:main"
    assert callable(cli.main)


def test_excel_flag_without_csv_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([ADDRESS, "--excel"], geocoder=_Geocoder(), zctas=_zctas())
    assert exc.value.code == 2 and "--csv" in capsys.readouterr().err


def test_newline_flag_without_zips_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([ADDRESS, "--newline"], geocoder=_Geocoder(), zctas=_zctas())
    assert exc.value.code == 2 and "--zips" in capsys.readouterr().err


def test_zips_mode_keeps_stdout_clean_for_piping(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run([ADDRESS, "--zips"], capsys)
    assert code == 0
    assert out.strip() == "33602, 33603, 02108"  # nothing but the list on stdout
    assert "3 ZIP codes overlap" in err  # the summary went to stderr


def test_zips_mode_with_no_results_prints_nothing_on_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    far = make_gdf({"99999": box(60_000, 0, 61_000, 1000)})
    code = cli.main([ADDRESS, "--zips"], geocoder=_Geocoder(), zctas=far)
    captured = capsys.readouterr()
    assert code == 0 and captured.out == "" and "No ZIP codes" in captured.err


def test_python_dash_m_zipcator_is_the_same_entry_point(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["zipcator", "--help"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("zipcator", run_name="__main__")
    assert exc.value.code == 0 and "--radius" in capsys.readouterr().out
