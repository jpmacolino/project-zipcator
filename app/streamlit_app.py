"""Streamlit UI for Zipcator (docs/SPEC.md §5.4).

Layout and wiring only: every decision lives in ``src/zipcator`` (see ``zipcator.ui``,
``zipcator.export``, ``zipcator.mapview``), which is where it is tested.

Run: ``uv run streamlit run app/streamlit_app.py``
"""

from __future__ import annotations

import streamlit as st
import streamlit_folium

from zipcator import data, export, geo, mapview, ui
from zipcator import geocode as gc

st.set_page_config(page_title="Zipcator: ZIP codes within a radius", page_icon="📍", layout="wide")


@st.cache_resource(show_spinner="Loading ZIP boundaries…")
def _app_data() -> ui.AppData:
    return ui.load_app_data()


@st.cache_resource
def _geocoder() -> gc.Geocoder:
    # One instance for the whole server process, so its cache and the Nominatim rate limiter
    # are shared by every session (default_geocoder() builds a fresh cache on each call).
    return gc.default_geocoder()


@st.cache_data(show_spinner="Searching…", max_entries=64)
def _search(_zctas, token: str, lat: float, lon: float, radius: float, min_overlap: float):  # type: ignore[no-untyped-def]
    """Cached per (data version, center, radius, threshold); ``_zctas`` is not hashed."""
    return ui.run_search(_zctas, (lat, lon), radius, min_overlap)


def _radius_from_number() -> None:
    st.session_state["radius_slider"] = st.session_state["radius_num"]


def _radius_from_slider() -> None:
    st.session_state["radius_num"] = st.session_state["radius_slider"]


st.title("📍 Zipcator")
st.caption(
    "Every ZIP code that overlaps a radius around an address, with the share of each ZIP that "
    "lies inside the circle. " + ui.zcta_note()
)

try:
    app = _app_data()
except data.ZctaDataError as exc:
    st.error(ui.data_error_help(exc))
    st.stop()

# ----------------------------------------------------------------------------------- inputs
st.session_state.setdefault("radius_num", ui.DEFAULT_RADIUS_MI)
st.session_state.setdefault("radius_slider", ui.DEFAULT_RADIUS_MI)
st.session_state.setdefault("min_overlap", ui.DEFAULT_MIN_OVERLAP_PCT)

with st.sidebar:
    st.header("Search")
    # A form: pressing Enter submits, and typing never triggers a search (SPEC §5.1).
    with st.form("search_form", border=False):
        st.text_input(
            "Address, ZIP code, or lat, lon",
            key="address",
            placeholder="401 E Jackson St, Tampa, FL 33602",
            help="A one-line US address, a 5-digit ZIP, or coordinates like 27.9506, -82.4572.",
        )
        submitted = st.form_submit_button("Search", type="primary", width="stretch")
    st.number_input(
        "Radius (miles)",
        min_value=ui.MIN_RADIUS_MI,
        max_value=ui.MAX_RADIUS_MI,
        step=ui.RADIUS_STEP_MI,
        format="%.1f",
        key="radius_num",
        on_change=_radius_from_number,
    )
    st.slider(
        "Radius (miles)",
        min_value=ui.MIN_RADIUS_MI,
        max_value=ui.MAX_RADIUS_MI,
        step=ui.RADIUS_STEP_MI,
        key="radius_slider",
        on_change=_radius_from_slider,
        label_visibility="collapsed",
    )
    st.slider(
        "Minimum overlap (%)",
        min_value=0.0,
        max_value=100.0,
        step=1.0,
        key="min_overlap",
        help="Hide ZIPs with less than this share of their area inside the radius. "
        "0 shows any overlap. The ZIP containing the center is always shown.",
    )
    st.checkbox(
        "Show hidden ZIPs as grey outlines on the map",
        key="show_excluded",
        help="Only matters when the minimum overlap is above 0.",
    )

# ------------------------------------------------------------------------------------ search
if submitted:
    with st.spinner("Locating…"):
        try:
            located = gc.locate(st.session_state["address"], _geocoder(), app.zctas)
        except gc.GeocodeError as exc:
            st.session_state["located"] = None
            st.session_state["search_error"] = str(exc)
        else:
            st.session_state["located"] = located
            st.session_state["search_error"] = None
            st.session_state["candidate"] = 0  # a new search starts at the best match

error = st.session_state.get("search_error")
located = st.session_state.get("located")

if error:
    st.error(error)
elif located is None:
    st.info(
        "Enter an address, a ZIP code, or coordinates in the sidebar and press **Search**. "
        "Try `401 E Jackson St, Tampa, FL 33602`, `33602`, or `27.9506, -82.4572`."
    )
else:
    options = ui.candidate_options(located)
    if options:
        choice = st.selectbox(
            "Several addresses matched; choose one",
            range(len(options)),
            format_func=lambda i: options[i],
            key="candidate",
        )
        center = ui.pick_candidate(located, int(choice))
    else:
        center = located
    st.caption(ui.center_caption(center))

    radius = float(st.session_state["radius_num"])
    min_overlap = float(st.session_state["min_overlap"])
    try:
        result = _search(app.zctas, app.token, center.lat, center.lon, radius, min_overlap)
    except geo.AntimeridianError as exc:
        st.error(str(exc))
    else:
        st.markdown(f"#### {export.summary_line(result, center.matched_address)}")
        if result.count == 0:
            st.info(
                "No ZIP codes overlap this circle. Try a larger radius, or check that the "
                "location is inside the United States."
            )
        folium_map = mapview.build_map(
            result, center.matched_address, show_excluded=bool(st.session_state["show_excluded"])
        )
        # returned_objects=[]: panning or clicking the map must not rerun the app.
        streamlit_folium.st_folium(
            folium_map, height=520, width="stretch", returned_objects=[], key="map"
        )

        if result.count:
            table = export.result_table(result)
            st.dataframe(table, hide_index=True, width="stretch")
            if "Population" in table.columns:
                st.caption(
                    "Estimated population in the radius assumes people are spread evenly across "
                    "each ZIP, so treat it as a rough guide."
                )

            left, right = st.columns(2)
            filename = export.csv_filename(result)
            left.download_button(
                "⬇ Download CSV for Excel",
                export.csv_bytes(result, excel_safe=True),
                file_name=filename.replace(".csv", "_excel.csv"),
                mime="text/csv",
                key="download_excel",
                help='ZIPs are written as ="02108" so Excel keeps their leading zeros.',
                width="stretch",
            )
            right.download_button(
                "⬇ Download CSV (plain, for scripts)",
                export.csv_bytes(result),
                file_name=filename,
                mime="text/csv",
                key="download_plain",
                help="Standard CSV with text ZIPs; read it with dtype=str to keep leading zeros.",
                width="stretch",
            )

            st.subheader("ZIP list for ad platforms")
            style = st.radio(
                "Format", ["Comma-separated", "One per line"], horizontal=True, key="zip_format"
            )
            st.text_area(
                "Copy and paste into Meta, Google Ads, or your mail vendor",
                value=export.zip_list(result.matches["zip"], newline=style == "One per line"),
                height=120,
            )

st.divider()
st.caption(ui.footer_markdown(app.metadata))
