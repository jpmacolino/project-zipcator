"""Download, join, simplify, validate and write the ZCTA artifact (docs/SPEC.md §4).

``scripts/build_data.py`` is a thin wrapper around :func:`build`. All progress is reported
through ``logging``; failures raise :class:`BuildError` with an actionable message.
"""

from __future__ import annotations

import json
import logging
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import geopandas as gpd
import pandas as pd
import pyogrio
import requests
import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from zipcator import __version__, config, data
from zipcator.states import STATE_FIPS

logger = logging.getLogger(__name__)

BUILD_VERSION = "3"
DEFAULT_ACS_YEAR = 2023
SQ_M_PER_SQ_MI = 2_589_988.110336


#: Douglas-Peucker tolerance in degrees (~55 m at the equator), applied with *coverage*
#: simplification so neighbouring ZIPs keep a shared boundary (no gaps or overlaps). Chosen by
#: the study in docs/DECISIONS.md: ~40 MB artifact, worst overlap-% drift 0.13 pp.
DEFAULT_TOLERANCE = 0.0005
#: zstd shrinks the coordinate-heavy artifact ~45 % vs the snappy default.
PARQUET_OPTIONS: Mapping[str, Any] = {"compression": "zstd", "compression_level": 19}
_CHUNK = 1 << 20
_TIMEOUT = (15, 300)


_UPDATE_HINT = (
    "The data source may have moved. Find the current official location and update "
    "DEFAULT_SOURCES in src/zipcator/data_build.py (scripts/build_data.py wraps it), "
    "then note it in docs/DECISIONS.md."
)
_CACHE_HINT = (
    "If this file came from the cache it may be corrupt or an error page: re-run with --refresh."
)
_KEY_HINT = (
    "The Census API requires a (free) key: https://api.census.gov/data/key_signup.html . "
    "Pass --census-api-key KEY or set the CENSUS_API_KEY environment variable."
)


class BuildError(Exception):
    """The data build failed; the message says what to do about it."""


@dataclass(frozen=True)
class Sources:
    """Where the raw data comes from (SPEC §4.1). Verified 2026-11-08."""

    zcta_url: str
    county_rel_url: str
    geonames_url: str
    acs_url_template: str
    #: Separate GeoNames files for US territories (not in US.zip); same licence and location.
    geonames_extra_urls: tuple[str, ...] = ()


DEFAULT_SOURCES = Sources(
    zcta_url="https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip",
    county_rel_url=(
        "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
        "tab20_zcta520_county20_natl.txt"
    ),
    geonames_url="https://download.geonames.org/export/zip/US.zip",
    acs_url_template="https://api.census.gov/data/{year}/acs/acs5",
    geonames_extra_urls=tuple(
        f"https://download.geonames.org/export/zip/{code}.zip"
        for code in ("PR", "VI", "GU", "AS", "MP")
    ),
)


@dataclass(frozen=True)
class BuildResult:
    """The published artifact path and the metadata written next to it."""

    path: Path
    metadata: dict[str, Any]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


# ------------------------------------------------------------------------------- download
def download(
    url: str,
    dest: Path,
    *,
    refresh: bool = False,
    session: requests.Session | None = None,
) -> Path:
    """Download ``url`` to ``dest`` (cached; ``refresh=True`` re-downloads).

    Writes atomically (``.part`` then rename) plus a ``<dest>.meta.json`` sidecar with the
    URL, UTC download time and size, which ends up in ``metadata.json``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not refresh:
        cached_url = _read_sidecar(dest).get("url")
        if cached_url in (None, url):  # None: hand-placed file, trusted
            logger.info("using cached %s (pass --refresh to re-download)", dest.name)
            return dest
        logger.info("cached %s came from a different URL; re-downloading", dest.name)
    logger.info("downloading %s", url)
    part = dest.with_name(dest.name + ".part")
    getter = session or requests
    try:
        with getter.get(
            url, stream=True, timeout=_TIMEOUT, headers={"User-Agent": config.user_agent()}
        ) as resp:
            if resp.status_code != 200:
                raise BuildError(f"{url} returned HTTP {resp.status_code}. {_UPDATE_HINT}")
            size = 0
            with part.open("wb") as fh:
                for chunk in resp.iter_content(_CHUNK):
                    fh.write(chunk)
                    size += len(chunk)
    except requests.RequestException as exc:
        raise BuildError(
            f"network error downloading {url}: {exc}. Check your connection and re-run."
        ) from exc
    else:
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)  # no-op after a successful replace
    sidecar = {"url": url, "downloaded_at": _now(), "bytes": size}
    dest.with_name(dest.name + ".meta.json").write_text(json.dumps(sidecar), encoding="utf-8")
    logger.info("saved %s (%.1f MB)", dest.name, size / 1e6)
    return dest


def _read_sidecar(path: Path) -> dict[str, Any]:
    try:
        info = json.loads(path.with_name(path.name + ".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def _source_record(name: str, url: str, path: Path) -> dict[str, Any]:
    info = _read_sidecar(path)
    if "downloaded_at" in info:
        stamp, origin = info["downloaded_at"], "download_sidecar"
        size = info.get("bytes")
    else:  # hand-placed file: the best available timestamp is its mtime
        stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        origin, size = "file_mtime", path.stat().st_size
    return {
        "name": name,
        "url": url,
        "downloaded_at": stamp,
        "timestamp_source": origin,
        "bytes": size,
    }


# --------------------------------------------------------------------------------- readers
def _require_zip(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise BuildError(
            f"{path.name} is not a valid ZIP file (the download may be an error page). "
            "Re-run with --refresh; if it persists the URL may have moved."
        )


def read_boundaries(path: Path) -> gpd.GeoDataFrame:
    """D1: ZCTA polygons (EPSG:4326) with ``zip`` and ``land_area_sq_mi`` (Census ALAND20)."""
    _require_zip(path)
    try:
        raw = pyogrio.read_dataframe(f"zip://{path.resolve().as_posix()}")
    except Exception as exc:
        raise BuildError(f"could not read the boundary shapefile in {path.name}: {exc}") from exc
    missing = [c for c in ("ZCTA5CE20", "ALAND20") if c not in raw.columns]
    if missing:
        raise BuildError(
            f"schema mismatch in {path.name}: missing column(s) {', '.join(missing)} "
            f"(found: {', '.join(map(str, raw.columns))}). The Census may have changed the format."
        )
    gdf = raw.set_crs(4326) if raw.crs is None else raw.to_crs(4326)
    out = gpd.GeoDataFrame(
        {
            "zip": gdf["ZCTA5CE20"].astype(str).str.strip(),
            # Census ALAND20 (land area, unclipped by our simplification). Documented in
            # docs/DECISIONS.md: search uses projected polygon area for overlap %, not this.
            "land_area_sq_mi": pd.to_numeric(gdf["ALAND20"]).astype(float) / SQ_M_PER_SQ_MI,
        },
        geometry=gdf.geometry.to_numpy(),
        crs="EPSG:4326",
    )
    logger.info("read %s ZCTA polygons", f"{len(out):,}")
    return out


def read_county_relationship(path: Path) -> pd.DataFrame:
    """D2: primary state and county per ZCTA (county with the largest land overlap)."""
    needed = ["GEOID_ZCTA5_20", "GEOID_COUNTY_20", "NAMELSAD_COUNTY_20", "AREALAND_PART",
              "AREAWATER_PART"]  # fmt: skip
    try:
        df = pd.read_csv(path, sep="|", dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise BuildError(f"could not read {path.name}: {exc}. {_CACHE_HINT}") from exc
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise BuildError(
            f"schema mismatch in {path.name}: missing column(s) {', '.join(missing)}. "
            f"The Census may have changed the relationship-file format. {_CACHE_HINT}"
        )
    df = df[df["GEOID_ZCTA5_20"].str.strip() != ""].copy()  # counties with no ZCTA
    df["land"] = pd.to_numeric(df["AREALAND_PART"], errors="coerce").fillna(0)
    df["water"] = pd.to_numeric(df["AREAWATER_PART"], errors="coerce").fillna(0)
    # Largest land overlap wins; water-only ZCTAs (all land == 0) fall back to water overlap.
    df = df.sort_values(
        ["GEOID_ZCTA5_20", "land", "water", "GEOID_COUNTY_20"],
        ascending=[True, False, False, True],
    ).drop_duplicates("GEOID_ZCTA5_20")
    out = pd.DataFrame(
        {
            "zip": df["GEOID_ZCTA5_20"].str.strip().str.zfill(5),
            "state": df["GEOID_COUNTY_20"].str[:2].map(STATE_FIPS),
            "county": df["NAMELSAD_COUNTY_20"],
        }
    )
    return out.reset_index(drop=True)


_GEONAMES_COLUMNS = [
    "country", "zip", "city", "state_name", "state_code", "county_name",
    "county_code", "admin3_name", "admin3_code", "lat", "lon", "accuracy",
]  # fmt: skip


def read_geonames(path: Path) -> pd.DataFrame:
    """D3: place name per ZIP (first row per postal code). CC BY 4.0 data."""
    _require_zip(path)
    member = f"{path.stem}.txt"  # US.zip -> US.txt, PR.zip -> PR.txt
    try:
        with zipfile.ZipFile(path) as zf, zf.open(member) as fh:
            df = pd.read_csv(
                fh, sep="\t", header=None, names=_GEONAMES_COLUMNS, dtype=str,
                keep_default_na=False, encoding="utf-8",
            )  # fmt: skip
    except (
        KeyError,
        OSError,
        UnicodeDecodeError,
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
    ) as exc:
        raise BuildError(f"could not read {member} from {path.name}: {exc}. {_CACHE_HINT}") from exc
    if df.empty:
        raise BuildError(f"{member} in {path.name} has no rows. {_CACHE_HINT}")
    out = pd.DataFrame({"zip": df["zip"].str.strip().str.zfill(5), "city": df["city"].str.strip()})
    return out.drop_duplicates("zip", keep="first").reset_index(drop=True)


def fetch_population(
    *,
    year: int,
    api_key: str | None,
    acs_url_template: str = DEFAULT_SOURCES.acs_url_template,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """D4 (optional): ACS 5-year total population (B01003_001E) per ZCTA."""
    url = acs_url_template.format(year=year)
    params = {"get": "B01003_001E", "for": "zip code tabulation area:*"}
    if api_key:
        params["key"] = api_key
    getter = session or requests
    logger.info("fetching ACS %d population by ZCTA", year)
    failure: str | None = None
    try:
        resp = getter.get(
            url, params=params, timeout=_TIMEOUT, headers={"User-Agent": config.user_agent()}
        )
    except requests.RequestException as exc:
        failure = _scrub(str(exc), api_key)
    if failure is not None:
        # Raised outside the except block on purpose: no __cause__/__context__ may carry the
        # raw message, which can embed the request URL and so the API key.
        raise BuildError(f"network error calling the Census API: {failure}")
    if resp.status_code != 200:
        raise BuildError(f"Census API {url} returned HTTP {resp.status_code}. {_KEY_HINT}")
    try:
        rows = json.loads(resp.text)
        header, body = rows[0], rows[1:]
        frame = pd.DataFrame(body, columns=header)
        zips = frame["zip code tabulation area"].astype(str).str.zfill(5)
        values = pd.to_numeric(frame["B01003_001E"], errors="coerce")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise BuildError(f"the Census API did not return population data. {_KEY_HINT}") from exc
    values = values.where(values >= 0)  # negative codes (e.g. -666666666) mean "suppressed"
    return pd.DataFrame({"zip": zips, "population": values.astype("Int64")})


# --------------------------------------------------------------------------------- assemble
def _polygonal(geom: BaseGeometry | None) -> BaseGeometry | None:
    """Keep only polygon parts of a (repaired) geometry; ``None`` if nothing is left."""
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Polygon | MultiPolygon):
        return geom
    polys: list[Polygon] = []
    for part in shapely.get_parts(geom):
        if isinstance(part, Polygon) and not part.is_empty:
            polys.append(part)
        elif isinstance(part, MultiPolygon):
            polys.extend(p for p in part.geoms if not p.is_empty)
    if not polys:
        return None
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def _with_text_zip(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["zip"] = out["zip"].astype(str)
    return out


def simplify_coverage(geoms: gpd.GeoSeries, tolerance: float) -> gpd.GeoSeries:
    """Topology-preserving *coverage* simplification (``shapely.coverage_simplify``).

    Every edge shared by two neighbouring polygons is simplified once and identically for both,
    so a clean partition (the Census ZCTA file has 0 km² of overlap) stays a clean partition:
    no new gaps or overlaps. Per-polygon ``simplify`` cannot guarantee that. Null/empty entries
    pass through unchanged.
    """
    arr = geoms.to_numpy().copy()
    usable = ~(shapely.is_missing(arr) | shapely.is_empty(arr))
    arr[usable] = shapely.coverage_simplify(arr[usable], tolerance)
    return gpd.GeoSeries(arr, index=geoms.index, crs=geoms.crs)


def assemble(
    boundaries: gpd.GeoDataFrame,
    rel: pd.DataFrame,
    geonames: pd.DataFrame,
    population: pd.DataFrame | None = None,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> gpd.GeoDataFrame:
    """Join the sources, repair geometry, simplify (``tolerance`` degrees; 0 = off), add points."""
    gdf = boundaries.merge(_with_text_zip(rel), on="zip", how="left")
    gdf = gdf.merge(_with_text_zip(geonames[["zip", "city"]]), on="zip", how="left")
    if population is not None:
        gdf = gdf.merge(_with_text_zip(population[["zip", "population"]]), on="zip", how="left")
    else:
        gdf["population"] = pd.NA
    gdf["population"] = gdf["population"].astype("Int64")

    # Rows are never dropped here: a ZCTA that lacks a state or a polygon is reported by the
    # SPEC §4.3 validation, which fails the build loudly instead of silently losing ZIPs.
    geoms = gdf.geometry
    invalid = ~geoms.is_valid & ~geoms.is_empty & ~geoms.isna()
    if invalid.any():
        logger.info("repairing %d invalid geometries", int(invalid.sum()))
        geoms = geoms.copy()
        geoms[invalid] = shapely.make_valid(geoms[invalid].to_numpy())
    geoms = gpd.GeoSeries([_polygonal(g) for g in geoms], index=geoms.index, crs=geoms.crs)
    if tolerance > 0:
        geoms = simplify_coverage(geoms, tolerance)
    gdf = gdf.assign(geometry=geoms).set_geometry("geometry")

    usable = gdf.geometry.notna() & ~gdf.geometry.is_empty
    rep = gdf.geometry.where(usable).representative_point()
    gdf["intpt_lon"] = rep.x
    gdf["intpt_lat"] = rep.y
    cols = ["zip", "state", "county", "city", "land_area_sq_mi", "intpt_lat", "intpt_lon",
            "population", "geometry"]  # fmt: skip
    return gpd.GeoDataFrame(gdf[cols].reset_index(drop=True), geometry="geometry", crs="EPSG:4326")


# -------------------------------------------------------------------------------------- build
def _write_atomic(path: Path, writer: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        writer(tmp)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def build(
    data_dir: Path,
    *,
    refresh: bool = False,
    with_population: bool = False,
    census_api_key: str | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    sources: Sources = DEFAULT_SOURCES,
    session: requests.Session | None = None,
    min_rows: int = data.MIN_ROWS,
    acs_year: int = DEFAULT_ACS_YEAR,
) -> BuildResult:
    """Run the whole pipeline and publish ``zcta.parquet`` + ``metadata.json`` in ``data_dir``.

    Idempotent: raw downloads in ``data_dir/raw`` are reused unless ``refresh``. The artifact
    is validated *before* it replaces any existing one.
    """
    if with_population and not census_api_key:
        raise BuildError(f"--with-population needs a Census API key. {_KEY_HINT}")
    data_dir = Path(data_dir)
    raw = data_dir / "raw"
    data_dir.mkdir(parents=True, exist_ok=True)

    def fetch(url: str) -> Path:
        return download(url, raw / Path(urlparse(url).path).name, refresh=refresh, session=session)

    zcta_zip = fetch(sources.zcta_url)
    rel_txt = fetch(sources.county_rel_url)
    geonames_zip = fetch(sources.geonames_url)
    extra_zips = [(url, fetch(url)) for url in sources.geonames_extra_urls]

    logger.info("reading sources")
    boundaries = read_boundaries(zcta_zip)
    rel = read_county_relationship(rel_txt)
    # US file first: on a conflict it wins; territory files only fill ZIPs it lacks.
    geonames = pd.concat(
        [read_geonames(geonames_zip), *(read_geonames(p) for _, p in extra_zips)],
        ignore_index=True,
    ).drop_duplicates("zip", keep="first")
    population = None
    pop_downloaded = None
    if with_population:
        population = fetch_population(
            year=acs_year,
            api_key=census_api_key,
            acs_url_template=sources.acs_url_template,
            session=session,
        )
        pop_downloaded = _now()

    logger.info("assembling (coverage simplification tolerance %g degrees)", tolerance)
    gdf = assemble(boundaries, rel, geonames, population, tolerance=tolerance)
    blank_cities = float(gdf["city"].isna().mean())
    if blank_cities > 0.1:
        logger.warning(
            "%.0f%% of ZIPs have no city name; is the GeoNames file empty or in a new format?",
            blank_cities * 100,
        )

    logger.info("validating %s rows", f"{len(gdf):,}")
    problems = data.validate_zcta_frame(gdf, min_rows=min_rows, check_geometry_validity=True)
    if problems:
        raise BuildError(
            "output validation failed: " + "; ".join(problems) + ". "
            "Nothing was written. Re-run with --refresh; if it persists the source format changed."
        )

    source_records = [
        _source_record("zcta_boundaries", sources.zcta_url, zcta_zip),
        _source_record("county_relationship", sources.county_rel_url, rel_txt),
        _source_record("geonames", sources.geonames_url, geonames_zip),
        *(_source_record(f"geonames_{p.stem}", url, p) for url, p in extra_zips),
    ]
    if pop_downloaded:
        source_records.append(
            {
                "name": "acs5_population",
                "url": sources.acs_url_template.format(year=acs_year),
                "downloaded_at": pop_downloaded,
                "bytes": None,
            }
        )
    geonames_date = source_records[2]["downloaded_at"][:10]  # index 2 = the US file
    metadata: dict[str, Any] = {
        "built_at": _now(),
        "zipcator_version": __version__,
        "build_script_version": BUILD_VERSION,
        "row_count": len(gdf),
        "input_zcta_count": len(boundaries),
        "simplification": (
            None
            if tolerance <= 0
            else {
                "method": "shapely.coverage_simplify (shared edges simplified once)",
                "tolerance_deg": tolerance,
            }
        ),
        "parquet_options": dict(PARQUET_OPTIONS),
        "population_included": population is not None,
        "land_area_source": "Census ALAND20 (square meters) converted to square miles",
        "sources": source_records,
        "vintages": {
            "zcta_boundaries": "2020 ZCTA5 (ZCTA520), cartographic boundary 1:500k",
            "county_relationship": "2020 ZCTA520-to-County relationship file",
            "geonames": f"GeoNames US postal codes, downloaded {geonames_date} (CC BY 4.0)",
            "acs5_population": f"ACS 5-year {acs_year}, B01003_001E"
            if population is not None
            else None,
        },
    }
    out = data_dir / "zcta.parquet"
    _write_atomic(out, lambda p: gdf.to_parquet(p, **PARQUET_OPTIONS))
    _write_atomic(
        data_dir / "metadata.json",
        lambda p: p.write_text(json.dumps(metadata, indent=2), encoding="utf-8"),
    )
    logger.info("wrote %s (%s ZCTAs)", out, f"{len(gdf):,}")
    return BuildResult(path=out, metadata=metadata)
