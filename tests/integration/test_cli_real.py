"""The CLI end to end on the REAL artifact (marker ``data``; no network: coordinates and ZIPs).

Expected ZIPs are well-known facts about downtown Tampa and downtown Boston, not whatever the
code printed the first time.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from zipcator import cli

pytestmark = pytest.mark.data


class _NoNetwork:
    def geocode(self, query: str):  # type: ignore[no-untyped-def]
        raise AssertionError("coordinate and ZIP input must not geocode")


def _run(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(args, geocoder=_NoNetwork())  # zctas=None: loads the real artifact
    out = capsys.readouterr()
    return code, out.out, out.err


def test_downtown_tampa_coordinates(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["27.9506, -82.4572", "--radius", "5"], capsys)
    assert code == 0 and err == ""
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert "ZIP codes overlap a 5-mile radius" in lines[0]
    assert lines[2].split()[0] == "33602"  # downtown Tampa's ZIP contains the point: listed first
    for z in ("33602", "33606", "33605", "33607"):  # Tampa-area ZIPs within 5 miles
        assert z in out


def test_boston_zips_keep_their_leading_zeros_in_table_and_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "boston.csv"
    code, out, _ = _run(["42.3601, -71.0589", "--radius", "3", "--csv", str(target)], capsys)
    assert code == 0
    downtown = {"02108", "02109", "02110", "02111"}  # Beacon Hill, North End/Waterfront, ...
    assert all(z in out for z in downtown)
    rows = list(csv.reader(io.StringIO(target.read_bytes().decode("utf-8-sig"), newline="")))
    csv_zips = {r[0] for r in rows[1:]}
    assert downtown <= csv_zips
    assert all(len(z) == 5 and z.isdigit() for z in csv_zips)


def test_bare_zip_input_centres_on_that_zcta(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(["02108", "--radius", "1"], capsys)
    assert code == 0 and "ZIP 02108 (approximate center)" in out
    assert "02108" in out


def test_zips_list_mode_on_real_data(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["27.9506, -82.4572", "--radius", "3", "--zips"], capsys)
    zips = [z.strip() for z in out.strip().split(",")]
    assert code == 0 and zips[0] == "33602" and len(zips) == len(set(zips)) >= 5
    assert "overlap a 3-mile radius" in err


def test_honolulu_and_san_juan_work_through_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(["21.3069, -157.8583", "--radius", "10"], capsys)
    assert code == 0 and "968" in out  # Honolulu ZIPs are 968xx
    code, out, _ = _run(["18.4655, -66.1057", "--radius", "10"], capsys)
    assert code == 0 and "009" in out and "PR" in out  # San Juan ZIPs are 009xx


def test_outside_the_us_is_a_clean_error_on_real_data(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = _run(["51.5074, -0.1278", "--radius", "5"], capsys)
    assert code == 1 and out == "" and "outside the US" in err
