"""Visual and real-browser QA (marker ``visual``; manual): drive the real app in Chromium.

Run once after ``uv sync --group visual`` and ``uv run playwright install chromium``:

    uv run pytest -m visual

Screenshots go to ``qa/screenshots/`` (gitignored) and MUST be opened and looked at (CLAUDE.md,
SPEC §8.4). These tests also assert what AppTest cannot see because it replaces ``st_folium``
with a recorder: that tooltips work inside the circle, that the map really drew one polygon per
table row, that the circle's pixel size matches the map scale (§9.5), that downloads contain the
right bytes, and that hostile address text neither runs script nor renders as markup.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.visual, pytest.mark.data]

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "qa" / "screenshots"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def _serve(script: Path) -> Iterator[str]:
    port = _free_port()
    env = {**os.environ, "ZIPCATOR_DISABLE_NOMINATIM": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(script),
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
            "--server.fileWatcherType",
            "none",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                with urllib.request.urlopen(f"{url}/_stcore/health", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("the Streamlit server did not start within 60 s")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def app_url() -> Iterator[str]:
    with _serve(ROOT / "app" / "streamlit_app.py") as url:
        yield url


@pytest.fixture(scope="module")
def browser():  # type: ignore[no-untyped-def]
    sync_api = pytest.importorskip("playwright.sync_api", reason="uv sync --group visual")
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment specific
            pytest.skip(f"Chromium is not installed (uv run playwright install chromium): {exc}")
        yield b
        b.close()


def _open(browser, url: str, *, width: int = 1400, height: int = 1000):  # type: ignore[no-untyped-def]
    page = browser.new_page(viewport={"width": width, "height": height})
    page.goto(url)
    page.get_by_text("Zipcator").first.wait_for(timeout=30_000)
    return page


def _search(page, text: str, radius: float, *, min_overlap: int | None = None) -> None:  # type: ignore[no-untyped-def]
    num = page.get_by_role("spinbutton", name="Radius (miles)")
    num.fill(str(radius))
    num.press("Enter")
    page.wait_for_timeout(800)
    if min_overlap is not None:
        slider = page.get_by_role("slider", name="Minimum overlap (%)")
        slider.focus()
        for _ in range(min_overlap):
            slider.press("ArrowRight")
        page.wait_for_timeout(800)
    page.get_by_role("textbox", name="Address, ZIP code, or lat, lon").fill(text)
    # scoped to the form: once a table exists its toolbar has another button named "Search"
    page.locator("[data-testid=stForm]").get_by_role("button", name="Search").click()
    page.get_by_text(re.compile(r"ZIP codes? overlaps?|No ZIP codes overlap")).first.wait_for(
        timeout=60_000
    )


def _map_frame(page):  # type: ignore[no-untyped-def]
    frame_el = page.locator("iframe").first
    frame_el.wait_for(timeout=30_000)
    frame = frame_el.content_frame
    frame.locator(".leaflet-container").wait_for(timeout=30_000)
    frame.locator(".leaflet-tile-loaded").first.wait_for(timeout=30_000)
    page.wait_for_timeout(2500)  # let tiles and fitBounds settle
    return frame


def _shot(page, name: str) -> Path:  # type: ignore[no-untyped-def]
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    assert path.stat().st_size > 20_000
    return path


def _table_rows(page) -> int:  # type: ignore[no-untyped-def]
    # st.dataframe draws on a canvas; the summary line states the count authoritatively.
    text = page.get_by_text(re.compile(r"\d+ ZIP codes? overlaps?")).first.inner_text()
    return int(re.match(r"(\d+)", text.strip("# *").strip()).group(1))  # type: ignore[union-attr]


def _assert_map(frame, expected_zips: int) -> None:  # type: ignore[no-untyped-def]
    paths = frame.locator("path.leaflet-interactive").count()
    # one polygon per result ZIP + the radius circle (a polygon, not an L.circle)
    assert paths == expected_zips + 1, f"{paths} vector layers for {expected_zips} ZIPs"
    assert frame.locator("path.zipcator-radius").count() == 1
    assert frame.locator(".leaflet-marker-pane > *").count() == 1  # the center marker
    assert frame.get_by_text("Overlap with the radius").count() == 1  # our legend, not just zoom


def _scale_px_per_mile(frame) -> float:  # type: ignore[no-untyped-def]
    """Pixels per mile from Leaflet's own imperial scale bar."""
    for line in frame.locator(".leaflet-control-scale-line").all():
        text = line.inner_text().strip()
        m = re.fullmatch(r"([\d,.]+)\s*(mi|ft)", text)
        if m:
            amount = float(m.group(1).replace(",", ""))
            miles = amount if m.group(2) == "mi" else amount / 5280
            width_px = float(line.evaluate("el => parseFloat(el.style.width)"))
            return width_px / miles
    raise AssertionError("no imperial scale bar found")


def _assert_circle_matches_scale(frame, radius_mi: float) -> None:  # type: ignore[no-untyped-def]
    """SPEC §9.5: the drawn circle's width equals 2 x radius on the map's own scale bar."""
    width = frame.locator("path.zipcator-radius").bounding_box()["width"]
    expected = 2 * radius_mi * _scale_px_per_mile(frame)
    assert abs(width - expected) / expected < 0.04, f"circle {width:.0f}px vs {expected:.0f}px"


# ======================================================================= the spec's scenarios
def test_scenario_1_dense_urban_tampa_5_miles(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url)
    _search(page, "401 E Jackson St, Tampa, FL 33602", 5)
    frame = _map_frame(page)
    _assert_map(frame, _table_rows(page))
    _assert_circle_matches_scale(frame, 5)
    assert page.get_by_text("Center: 401 JACKSON ST, TAMPA, FL, 33602").count() == 1
    _shot(page, "01_tampa_5mi")
    # the results table and the export controls are below the fold: look at them too
    page.get_by_text("ZIP list for ad platforms").scroll_into_view_if_needed()
    page.wait_for_timeout(800)
    assert page.get_by_role("button", name="⬇ Download CSV for Excel").count() == 1
    assert page.get_by_role("button", name="⬇ Download CSV (plain, for scripts)").count() == 1
    copy_box = page.get_by_role("textbox", name=re.compile("Copy and paste"))
    assert copy_box.input_value().startswith("33602")
    _shot(page, "01b_tampa_table_and_exports")


def test_scenario_2_rural_okeechobee_10_miles(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url)
    _search(page, "27.2439, -80.8298", 10)
    frame = _map_frame(page)
    _assert_map(frame, _table_rows(page))
    _assert_circle_matches_scale(frame, 10)
    _shot(page, "02_okeechobee_10mi")


def test_scenario_3_coastal_miami_beach_5_miles(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url)
    _search(page, "25.7907, -80.1300", 5)
    frame = _map_frame(page)
    _assert_map(frame, _table_rows(page))
    _assert_circle_matches_scale(frame, 5)
    _shot(page, "03_miami_beach_5mi")


def test_min_overlap_with_grey_outlines(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url)
    _search(page, "27.9506, -82.4572", 10, min_overlap=30)
    page.get_by_text("Show hidden ZIPs (grey outlines)").click()
    page.wait_for_timeout(2500)
    frame = _map_frame(page)
    n = _table_rows(page)
    assert frame.locator("path.leaflet-interactive").count() > n + 1  # + grey outlines
    _shot(page, "04_tampa_10mi_min30_grey")


def test_empty_state_and_error_state(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url)
    _search(page, "27.0, -85.0", 5)  # open Gulf of Mexico
    page.get_by_text("No ZIP codes overlap this circle").wait_for(timeout=30_000)
    frame = _map_frame(page)  # the empty state still draws the circle and the marker
    assert frame.locator("path.leaflet-interactive").count() == 1
    _shot(page, "05_empty_gulf")
    page.get_by_role("textbox", name="Address, ZIP code, or lat, lon").fill("95, 10")
    page.locator("[data-testid=stForm]").get_by_role("button", name="Search").click()
    page.get_by_text(re.compile("latitude")).first.wait_for(timeout=30_000)
    _shot(page, "06_error_invalid_coordinates")


def test_phone_width_layout_has_no_horizontal_scroll(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url, width=390, height=844)
    _shot(page, "07_phone_first_load")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"page scrolls horizontally by {overflow}px at phone width"


def test_phone_width_with_results_fits_without_horizontal_scroll(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url, width=390, height=844)
    _search(page, "27.9506, -82.4572", 5)
    frame = _map_frame(page)
    _assert_map(frame, _table_rows(page))
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    _shot(page, "08_phone_results")
    assert overflow <= 1, f"page scrolls horizontally by {overflow}px at phone width"


# ============================================================== phase 5 QA review: real browser
def test_zip_tooltips_work_inside_the_radius_circle(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    """The filled circle used to sit above the ZIP layer and swallow every hover, so only
    '5-mile radius' showed inside it and the ZIP / city / overlap tooltip was unreachable."""
    page = _open(browser, app_url, height=1500)
    _search(page, "27.9506, -82.4572", 5)
    frame = _map_frame(page)
    page.locator("iframe").first.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    box = frame.locator("path.zipcator-radius").bounding_box()
    cx, cy, r = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, box["width"] / 2
    tips: list[str] = []
    for dx in (-0.5, -0.25, 0.0, 0.25, 0.5):
        for dy in (-0.5, -0.25, 0.0, 0.25, 0.5):
            page.mouse.move(cx + dx * r, cy + dy * r)
            page.wait_for_timeout(120)
            tips += frame.locator(".leaflet-tooltip").all_inner_texts()
    zip_tips = [t for t in tips if "ZIP" in t and "Overlap" in t]
    assert not any("mile radius" in t for t in tips), "the circle is capturing the mouse again"
    assert len(zip_tips) >= 5, f"only {len(zip_tips)} ZIP tooltips inside the circle: {set(tips)}"
    assert any(re.search(r"Overlap\s*\d+\.\d%", t) for t in zip_tips)


def test_downloads_contain_the_right_bytes_and_names(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url)
    _search(page, "02108", 3)
    page.get_by_text("ZIP list for ad platforms").scroll_into_view_if_needed()
    with page.expect_download() as excel_info:
        page.get_by_role("button", name="⬇ Download CSV for Excel").click()
    excel = excel_info.value
    with page.expect_download() as plain_info:
        page.get_by_role("button", name="⬇ Download CSV (plain, for scripts)").click()
    plain = plain_info.value
    excel_bytes, plain_bytes = Path(excel.path()).read_bytes(), Path(plain.path()).read_bytes()
    assert excel.suggested_filename.endswith("_excel.csv")
    assert plain.suggested_filename.endswith("mi.csv") and "_excel" not in plain.suggested_filename
    assert excel_bytes.startswith(b"\xef\xbb\xbf") and not plain_bytes.startswith(b"\xef\xbb\xbf")
    assert b'="02108"' in excel_bytes.replace(b'""', b'"')  # the RFC-escaped text formula
    first_cell = plain_bytes.split(b"\r\n")[1].split(b",")[0]
    assert first_cell.startswith(b'"') and b"=" not in first_cell  # plain: quoted text ZIP


def test_hostile_map_label_does_not_break_the_map_or_run_script(browser, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """A matched address with a backtick / ${...} used to end folium's JS template literal:
    the iframe threw 'missing ) after argument list' and no map rendered (and ${alert(1)} ran)."""
    sys.path.insert(0, str(ROOT / "tests"))
    from shapely.geometry import box
    from zc_testkit.synth import TAMPA, make_gdf

    from zipcator import mapview
    from zipcator.search import find_zips

    res = find_zips(make_gdf({"33602": box(-1000, -1000, 1000, 1000)}), TAMPA, 5)
    label = "x`);window.__pwned=1;//${window.__pwned=2}\\u00zz"
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    path = tmp_path / "map.html"
    path.write_text(str(mapview.build_map(res, label).get_root().render()), encoding="utf-8")
    page.goto(path.as_uri())
    page.locator(".leaflet-container").wait_for(timeout=30_000)
    page.wait_for_timeout(1500)
    assert errors == []
    assert page.evaluate("window.__pwned === undefined")
    assert page.locator(".leaflet-marker-pane > *").count() == 1
    assert page.locator("path.zipcator-radius").count() == 1


def test_hostile_address_text_renders_as_literal_text_not_markup(browser, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Streamlit markdown turned '![i](http://host/x.png)' into an image the browser fetched."""
    script = tmp_path / "markup_probe.py"
    script.write_text(
        "import streamlit as st\n"
        "from zipcator import ui\n"
        "NASTY = '**BOLD** [click](javascript:alert(1)) ![i](http://127.0.0.1:9/x.png) `c` _it_'\n"
        "st.markdown('**' + ui.markdown_safe(NASTY) + '**')\n"
        "st.caption('Center: ' + ui.markdown_safe(NASTY))\n",
        encoding="utf-8",
    )
    with _serve(script) as url:
        page = browser.new_page()
        requests: list[str] = []
        page.on("request", lambda r: requests.append(r.url))
        page.goto(url)
        page.get_by_text("BOLD").first.wait_for(timeout=30_000)
        page.wait_for_timeout(1000)
        body = page.locator("[data-testid=stMain]")
        assert body.locator("img").count() == 0  # no image element from ![..](..)
        for link in body.locator("a").all():  # GFM auto-links a bare URL; it must not be disguised
            href = link.get_attribute("href") or ""
            assert link.inner_text() == href and not href.startswith("javascript:")
        assert not any("127.0.0.1:9" in r for r in requests)  # nothing was fetched
        text = body.inner_text()
        assert "**BOLD** [click](javascript:alert(1)) ![i](http://127.0.0.1:9/x.png)" in text
