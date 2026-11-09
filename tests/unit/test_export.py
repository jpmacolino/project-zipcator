"""Tests for zipcator.export: table, CSV, ZIP list, summary (SPEC §5.4, §8.2 export)."""

from __future__ import annotations

import csv
import io

import pandas as pd
import pytest
from shapely.geometry import box
from zc_testkit.synth import CENTERS, TAMPA, make_gdf

from zipcator import export
from zipcator.models import SearchResult
from zipcator.search import find_zips


def _result(population: bool = True, center: tuple[float, float] = TAMPA) -> SearchResult:
    zones = {
        "02108": box(-1000, -1000, 1000, 1000),  # home, leading zero
        "00501": box(3000, 3000, 6000, 6000),  # leading zeros
        "33602": box(0, 14_000, 20_000, 34_000),  # partial overlap
    }
    pops = {"02108": 3500, "00501": 600, "33602": 10_000} if population else None
    gdf = make_gdf(zones, center, population=pops)
    gdf.loc[gdf["zip"] == "00501", "city"] = 'Mayagüez, "Quoted"'  # accents, comma, quotes
    return find_zips(gdf, center, 10)


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


# ---------------------------------------------------------------------------- result_table
def test_display_table_has_the_spec_columns_in_order() -> None:
    df = export.result_table(_result())
    assert list(df.columns) == [
        "ZIP",
        "City",
        "County",
        "State",
        "Overlap %",
        "Area in radius (sq mi)",
        "Nearest distance (mi)",
        "Center distance (mi)",
        "Contains address",
        "Population",
        "Est. pop in radius",
    ]


def test_population_columns_are_omitted_when_no_population_data() -> None:
    df = export.result_table(_result(population=False))
    assert "Population" not in df.columns and "Est. pop in radius" not in df.columns


def test_zips_are_text_in_the_table() -> None:
    df = export.result_table(_result())
    assert df["ZIP"].tolist()[0] == "02108"
    assert all(isinstance(z, str) and len(z) == 5 for z in df["ZIP"])


def test_display_rounding_is_one_decimal_for_overlap_but_csv_keeps_precision() -> None:
    res = _result()
    shown = export.result_table(res)["Overlap %"]
    full = export.result_table(res, full=True)["Overlap %"]
    raw = res.matches["overlap_pct"]
    assert (shown == raw.round(1)).all()
    assert (full == raw.round(4)).all()
    assert any(f != s for f, s in zip(full, shown, strict=True))  # the partial ZIP differs


def test_contains_address_is_yes_or_no() -> None:
    df = export.result_table(_result())
    assert df["Contains address"].tolist()[0] == "Yes"
    assert set(df["Contains address"]) == {"Yes", "No"}


def test_full_table_adds_zip_area() -> None:
    assert "ZIP area (sq mi)" in export.result_table(_result(), full=True).columns
    assert "ZIP area (sq mi)" not in export.result_table(_result()).columns


def test_est_population_is_rounded_to_whole_people_for_display() -> None:
    df = export.result_table(_result())
    est = df["Est. pop in radius"].dropna()
    assert (est == est.round(0)).all()


def test_missing_values_stay_blank_not_nan_strings() -> None:
    gdf = make_gdf({"02108": box(-1000, -1000, 1000, 1000)}).drop(columns=["city"])
    df = export.result_table(find_zips(gdf, TAMPA, 10))
    assert df["City"].isna().all() or (df["City"] == "").all()


def test_empty_result_gives_an_empty_table_with_the_columns() -> None:
    gdf = make_gdf({"99999": box(60_000, 0, 61_000, 1000)})
    df = export.result_table(find_zips(gdf, TAMPA, 10))
    assert df.empty and "ZIP" in df.columns


# ------------------------------------------------------------------------------------- CSV
def test_csv_preserves_leading_zeros_when_read_back_as_text() -> None:
    text = export.to_csv(_result())
    rows = _rows(text)
    zips = [r[0] for r in rows[1:]]
    assert "02108" in zips and "00501" in zips
    # and pandas, told to keep text, agrees
    df = pd.read_csv(io.StringIO(text), dtype={"ZIP": str})
    assert df["ZIP"].tolist() == zips


def test_csv_quotes_text_fields_so_zip_is_unambiguously_a_string() -> None:
    first_data_line = export.to_csv(_result()).splitlines()[1]
    assert first_data_line.startswith('"02108"')  # quoted: a text cell, never a bare number


def test_csv_header_and_all_columns_present() -> None:
    rows = _rows(export.to_csv(_result()))
    assert rows[0] == list(export.result_table(_result(), full=True).columns)
    assert all(len(r) == len(rows[0]) for r in rows)


def test_csv_handles_commas_quotes_and_accents_in_values() -> None:
    rows = _rows(export.to_csv(_result()))
    cities = {r[0]: r[1] for r in rows[1:]}
    assert cities["00501"] == 'Mayagüez, "Quoted"'


def test_csv_numbers_are_unquoted_and_parse_back() -> None:
    rows = _rows(export.to_csv(_result()))
    header = rows[0]
    pct = float(rows[1][header.index("Overlap %")])
    assert 0 < pct <= 100


def test_csv_for_empty_result_is_just_a_header() -> None:
    gdf = make_gdf({"99999": box(60_000, 0, 61_000, 1000)})
    rows = _rows(export.to_csv(find_zips(gdf, TAMPA, 10)))
    assert len(rows) == 1 and rows[0][0] == "ZIP"


def test_csv_bytes_start_with_a_utf8_bom_so_excel_reads_accents() -> None:
    raw = export.csv_bytes(_result())
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "Mayagüez" in raw.decode("utf-8-sig")


def test_csv_uses_crlf_line_endings_per_rfc4180() -> None:
    assert "\r\n" in export.to_csv(_result())


def test_excel_safe_csv_writes_zips_as_text_formulas_that_keep_leading_zeros() -> None:
    """Excel strips leading zeros even from quoted CSV values; ="02108" displays as text."""
    text = export.to_csv(_result(), excel_safe=True)
    zips = [r[0] for r in _rows(text)[1:]]
    assert '="02108"' in zips and '="00501"' in zips
    assert all(z.startswith('="') and z.endswith('"') and len(z) == 8 for z in zips)


def test_excel_safe_variant_only_changes_the_zip_column() -> None:
    plain = _rows(export.to_csv(_result()))
    safe = _rows(export.to_csv(_result(), excel_safe=True))
    assert [r[1:] for r in plain] == [r[1:] for r in safe]
    assert [r[0] for r in safe[1:]] == [f'="{r[0]}"' for r in plain[1:]]


@pytest.mark.parametrize("bad", ['=HYPERLINK("http://evil")', "+1+1", "-2+3", "@SUM(A1)", "\tx"])
def test_text_cells_are_defused_against_spreadsheet_formula_injection(bad: str) -> None:
    gdf = make_gdf({"02108": box(-1000, -1000, 1000, 1000)})
    gdf.loc[0, "city"] = bad
    rows = _rows(export.to_csv(find_zips(gdf, TAMPA, 10)))
    cell = rows[1][1]
    assert cell.startswith("'") and cell[1:] == bad


def test_ordinary_text_is_not_altered_by_the_defusing() -> None:
    assert export.defuse("Tampa") == "Tampa" and export.defuse("") == ""
    assert export.defuse("St. Marys") == "St. Marys"


def test_csv_filename_is_filesystem_safe_and_descriptive() -> None:
    name = export.csv_filename(_result())
    assert name == "zipcator_27.9506_-82.4572_10mi.csv"
    other = export.csv_filename(
        find_zips(make_gdf({"02108": box(-1, -1, 1, 1)}, CENTERS["boston"]), CENTERS["boston"], 0.5)
    )
    assert other == "zipcator_42.3601_-71.0589_0.5mi.csv"
    assert not set(name) & set('<>:"/\\|?*')


# ---------------------------------------------------------------------------------- ZIP list
def test_zip_list_comma_separated() -> None:
    assert export.zip_list(["02108", "33602", "00501"]) == "02108, 33602, 00501"


def test_zip_list_newline_separated() -> None:
    assert export.zip_list(["02108", "33602"], newline=True) == "02108\n33602"


def test_zip_list_empty_input_gives_empty_string() -> None:
    assert export.zip_list([]) == "" and export.zip_list([], newline=True) == ""


def test_zip_list_keeps_leading_zeros_and_order_and_removes_duplicates() -> None:
    assert export.zip_list(["02108", "00501", "02108", "33602", "00501"]) == ("02108, 00501, 33602")


def test_zip_list_accepts_a_pandas_series() -> None:
    assert export.zip_list(pd.Series(["02108", "33602"])) == "02108, 33602"


@pytest.mark.parametrize("bad", [2108, "2108", "021080", "0210A", None, 33602])
def test_zip_list_rejects_anything_that_is_not_a_five_digit_string(bad: object) -> None:
    with pytest.raises(ValueError, match="ZIP"):
        export.zip_list(["33602", bad])  # type: ignore[list-item]


def test_zip_list_from_a_result_follows_the_table_order() -> None:
    res = _result()
    assert export.zip_list(res.matches["zip"]) == ", ".join(res.matches["zip"])


# --------------------------------------------------------------------------- summary line
def test_summary_line_plural_with_population() -> None:
    res = _result()
    line = export.summary_line(res, "401 JACKSON ST, TAMPA, FL, 33602")
    assert line.startswith("3 ZIP codes overlap a 10-mile radius around 401 JACKSON ST")
    assert "estimated" in line and "people" in line  # rough, uniform-density estimate


def test_summary_line_singular_and_fractional_radius() -> None:
    gdf = make_gdf({"02108": box(-50_000, -50_000, 50_000, 50_000)}, CENTERS["boston"])
    res = find_zips(gdf, CENTERS["boston"], 0.5)
    line = export.summary_line(res, "somewhere")
    assert line.startswith("1 ZIP code overlaps a 0.5-mile radius around somewhere")


def test_summary_line_without_population_has_no_population_clause() -> None:
    assert "people" not in export.summary_line(_result(population=False), "x")


def test_summary_line_flags_a_partial_population_total() -> None:
    zones = {"02108": box(-1000, -1000, 1000, 1000), "00501": box(3000, 3000, 6000, 6000)}
    gdf = make_gdf(zones, population={"02108": 3500})
    line = export.summary_line(find_zips(gdf, TAMPA, 10), "x")
    assert "not all" in line or "partial" in line


def test_summary_line_for_no_results_is_friendly() -> None:
    gdf = make_gdf({"99999": box(60_000, 0, 61_000, 1000)})
    line = export.summary_line(find_zips(gdf, TAMPA, 10), "the middle of nowhere")
    assert line.startswith("No ZIP codes") and "10-mile" in line


def test_summary_line_mentions_the_min_overlap_filter() -> None:
    gdf = make_gdf(
        {"02108": box(-1000, -1000, 1000, 1000), "33602": box(0, 14_000, 20_000, 34_000)}
    )
    line = export.summary_line(find_zips(gdf, TAMPA, 10, min_overlap_pct=50), "x")
    assert "at least 50%" in line
