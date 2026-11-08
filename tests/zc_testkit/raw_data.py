"""Tiny synthetic inputs in the exact shape of the real D1/D2/D3/D4 sources (SPEC §4.1).

Formats were verified against the real downloads (2026-11-08): D2 is a UTF-8-BOM,
pipe-delimited file whose leading rows are counties with no ZCTA (blank ZCTA fields);
D3 is a headerless tab-separated file inside ``US.zip``; D1 is a shapefile zip in NAD83
(EPSG:4269); D4 is a JSON array of arrays (or an HTML 'Missing Key' page without a key).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

REL_HEADER = (
    "OID_ZCTA5_20|GEOID_ZCTA5_20|NAMELSAD_ZCTA5_20|AREALAND_ZCTA5_20|AREAWATER_ZCTA5_20|"
    "MTFCC_ZCTA5_20|CLASSFP_ZCTA5_20|FUNCSTAT_ZCTA5_20|OID_COUNTY_20|GEOID_COUNTY_20|"
    "NAMELSAD_COUNTY_20|AREALAND_COUNTY_20|AREAWATER_COUNTY_20|MTFCC_COUNTY_20|"
    "CLASSFP_COUNTY_20|FUNCSTAT_COUNTY_20|AREALAND_PART|AREAWATER_PART"
)

# zip -> (name of the square's lower-left corner lon, lat); each square is 0.1 x 0.1 degrees
ZCTAS = {
    "02108": (-71.07, 42.35),  # Boston: leading zero
    "02109": (-71.05, 42.35),
    "33602": (-82.47, 27.94),  # Tampa; straddles two counties in the relationship file
    "33999": (-82.40, 27.80),  # water only: no land overlap rows
    "59001": (-109.0, 46.0),  # Montana: no GeoNames row -> city must be null
}

SQ_M_PER_SQ_MI = 2_589_988.110336


def _rel_row(zcta: str, county: str, name: str, land: int, water: int) -> str:
    z = (
        f"2759{zcta}|{zcta}|ZCTA5 {zcta}|{land}|{water}|G6350|B5|S"
        if zcta
        else "|||||||"  # county with no ZCTA: blank ZCTA columns, as in the real file
    )
    return f"{z}|2759{county}|{county}|{name}|1|1|G4020|H1|A|{land}|{water}"


def relationship_text(*, bom: bool = True) -> str:
    rows = [
        _rel_row("", "01003", "Baldwin County", 5, 5),  # leading blank-ZCTA row
        _rel_row("02108", "25025", "Suffolk County", 100_000, 10),
        _rel_row("02109", "25025", "Suffolk County", 200_000, 10),
        _rel_row("33602", "12103", "Pinellas County", 712_507, 50),  # smaller overlap
        _rel_row("33602", "12057", "Hillsborough County", 6_712_507, 935_367),  # primary
        _rel_row("33999", "12057", "Hillsborough County", 0, 500),
        _rel_row("33999", "12103", "Pinellas County", 0, 900),  # water tie-break -> Pinellas
        _rel_row("59001", "30111", "Yellowstone County", 9_000_000, 100),
        _rel_row("", "02013", "Aleutians East Borough", 7, 7),  # trailing blank-ZCTA row
    ]
    text = REL_HEADER + "\n" + "\n".join(rows) + "\n"
    return ("﻿" + text) if bom else text


def geonames_zip_bytes(*, drop: tuple[str, ...] = ("59001",)) -> bytes:
    rows = [
        ("02108", "Boston", "Massachusetts", "MA", "Suffolk", "025"),
        ("02108", "Boston Duplicate", "Massachusetts", "MA", "Suffolk", "025"),
        ("02109", "Boston", "Massachusetts", "MA", "Suffolk", "025"),
        ("33602", "Tampa", "Florida", "FL", "Hillsborough", "057"),
        ("33999", "Water City", "Florida", "FL", "Pinellas", "103"),
        ("59001", "Absarokee", "Montana", "MT", "Stillwater", "095"),
    ]
    lines = [
        "\t".join(["US", z, city, st, sc, cn, cc, "", "", "40.0", "-80.0", "4"])
        for z, city, st, sc, cn, cc in rows
        if z not in drop or city.endswith("Duplicate")
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("US.txt", "\n".join(lines) + "\n")
        zf.writestr("readme.txt", "GeoNames postal codes")
    return buf.getvalue()


def boundary_zip_bytes(tmp_dir: Path, *, drop_column: str | None = None) -> bytes:
    """A real shapefile zip (NAD83) with the real D1 column names."""
    rows = []
    for zip_code, (x, y) in ZCTAS.items():
        rows.append(
            {
                "ZCTA5CE20": zip_code,
                "AFFGEOID20": f"860Z200US{zip_code}",
                "GEOID20": zip_code,
                "NAME20": zip_code,
                "LSAD20": "Z5",
                "ALAND20": int(0.1 * 111_000 * 0.1 * 80_000),
                "AWATER20": 1000,
                "geometry": box(x, y, x + 0.1, y + 0.1),
            }
        )
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4269")
    if drop_column:
        gdf = gdf.drop(columns=[drop_column])
    shp_dir = tmp_dir / "shp"
    shp_dir.mkdir(parents=True, exist_ok=True)
    gdf.to_file(shp_dir / "cb_2020_us_zcta520_500k.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in sorted(shp_dir.iterdir()):
            zf.write(f, f.name)
    return buf.getvalue()


def acs_json(*, suppressed: tuple[str, ...] = ("33999",)) -> str:
    vals = {"02108": "3500", "02109": "1200", "33602": "7800", "33999": "0", "59001": "2100"}
    rows = [["B01003_001E", "zip code tabulation area"]]
    for z, v in vals.items():
        rows.append(["-666666666" if z in suppressed else v, z])
    return json.dumps(rows)


MISSING_KEY_HTML = "<html><head><title>Missing Key</title></head><body>A valid key...</body></html>"
