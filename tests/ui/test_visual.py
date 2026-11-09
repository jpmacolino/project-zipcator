"""Visual QA (marker ``visual``; manual): drive the real app in Chromium and save screenshots.

Run once after ``uv sync --group visual`` and ``uv run playwright install chromium``:

    uv run pytest -m visual

Screenshots go to ``qa/screenshots/`` (gitignored) and MUST be opened and looked at (CLAUDE.md,
SPEC §8.4). The test also asserts what a screenshot cannot prove on its own: that the map really
drew the geodesic circle and one polygon per table row.
"""

from __future__ import annotations

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


@pytest.fixture(scope="module")
def app_url() -> Iterator[str]:
    port = _free_port()
    env = {**os.environ, "ZIPCATOR_DISABLE_NOMINATIM": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "app" / "streamlit_app.py"),
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
    page.get_by_role("button", name="Search").click()
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
    return int(re.match(r"(\d+)", text.strip("# ").strip()).group(1))  # type: ignore[union-attr]


def _assert_map(page, frame, expected_zips: int) -> None:  # type: ignore[no-untyped-def]
    paths = frame.locator("path.leaflet-interactive").count()
    # one polygon per result ZIP + the radius circle (a polygon, not an L.circle)
    assert paths == expected_zips + 1, f"{paths} vector layers for {expected_zips} ZIPs"
    assert frame.locator(".leaflet-marker-pane > *").count() == 1  # the center marker
    assert frame.locator(".legend, .leaflet-control").count() >= 1


def test_scenario_1_dense_urban_tampa_5_miles(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url)
    _search(page, "401 E Jackson St, Tampa, FL 33602", 5)
    frame = _map_frame(page)
    n = _table_rows(page)
    _assert_map(page, frame, n)
    assert page.get_by_text("Center: 401 JACKSON ST, TAMPA, FL, 33602").count() == 1
    _shot(page, "01_tampa_5mi")
    # the results table and the export controls are below the fold: look at them too
    page.get_by_text("ZIP list for ad platforms").scroll_into_view_if_needed()
    page.wait_for_timeout(800)
    assert page.get_by_role("button", name="⬇ Download CSV for Excel").count() == 1
    assert page.get_by_role("button", name="⬇ Download CSV (plain, for scripts)").count() == 1
    assert (
        page.get_by_role("textbox", name=re.compile("Copy and paste"))
        .input_value()
        .startswith("33602")
    )
    _shot(page, "01b_tampa_table_and_exports")


def test_scenario_2_rural_okeechobee_10_miles(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url)
    _search(page, "27.2439, -80.8298", 10)
    frame = _map_frame(page)
    _assert_map(page, frame, _table_rows(page))
    _shot(page, "02_okeechobee_10mi")


def test_scenario_3_coastal_miami_beach_5_miles(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url)
    _search(page, "25.7907, -80.1300", 5)
    frame = _map_frame(page)
    _assert_map(page, frame, _table_rows(page))
    _shot(page, "03_miami_beach_5mi")


def test_min_overlap_with_grey_outlines(app_url: str, browser) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, app_url)
    _search(page, "27.9506, -82.4572", 10, min_overlap=30)
    page.get_by_text("Show hidden ZIPs as grey outlines on the map").click()
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
    page.get_by_role("button", name="Search").click()
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
    _assert_map(page, frame, _table_rows(page))
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    _shot(page, "08_phone_results")
    assert overflow <= 1, f"page scrolls horizontally by {overflow}px at phone width"
