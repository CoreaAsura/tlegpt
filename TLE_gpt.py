# app.py
import io
import textwrap
from datetime import datetime
from typing import List, Tuple

import requests
import streamlit as st
from skyfield.api import EarthSatellite, load

APP_TITLE = "CelesTrak → LEO TLE Filter"
DEFAULT_GROUP = "active"  # CelesTrak group
BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"
EARTH_RADIUS_KM = 6378.137

st.set_page_config(page_title=APP_TITLE, page_icon="🛰️", layout="centered")
st.title(APP_TITLE)
st.caption(
    "Fetch any CelesTrak group, filter LEO by perigee altitude, and export as .tle/.txt"
)

with st.expander("What counts as LEO here?", expanded=False):
    st.write(
        """
        **Definition used**: A satellite is treated as LEO if **perigee altitude** \(closest point to Earth\)
        is below your selected threshold (default **2000 km**). Perigee is computed from the TLE's
        semi-major axis \(a\) and eccentricity \(e\):

        \(\text{perigee\_alt} = a\,(1-e) - R_\oplus\), with \(R_\oplus=6378.137\,\text{km}\).
        """
    )

# ==== Sidebar controls ====
st.sidebar.header("Data source & filter")
source_mode = st.sidebar.radio(
    "Source",
    ["Common group", "Custom URL"],
    help="Use a standard CelesTrak group or paste a custom gp.php URL",
)

if source_mode == "Common group":
    group = st.sidebar.selectbox(
        "CelesTrak group",
        [
            "active",
            "stations",
            "visual",
            "resource",
            "weather",
            "science",
            "communication",
            "navigation",
            "geo",
            "last-30-days",
            "1999-025",  # Iridium 33/Cosmos 2251 debris example
        ],
        index=0,
        help="A few common groups. You can always switch to 'Custom URL' for anything else.",
    )
    params = {"GROUP": group, "FORMAT": "tle"}
else:
    custom_url = st.sidebar.text_input(
        "gp.php URL",
        value=f"{BASE_URL}?GROUP={DEFAULT_GROUP}&FORMAT=tle",
        help="Example: https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle",
    )

perigee_max_km = st.sidebar.slider(
    "LEO threshold (perigee altitude ≤ km)", 100, 3000, 2000, step=50
)

name_filter = st.sidebar.text_input(
    "Name contains (optional)",
    value="",
    help="Filter by substring in satellite name (case-insensitive)",
)

export_basename = st.sidebar.text_input(
    "Export filename (without extension)", value="LEO_only"
)


@st.cache_data(show_spinner=False)
def fetch_tle_text(params=None, custom_url: str | None = None) -> str:
    if custom_url:
        url = custom_url
    else:
        url = BASE_URL
    try:
        if custom_url:
            r = requests.get(url, timeout=30)
        else:
            r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.text
    except Exception as e:
        raise RuntimeError(f"Failed to fetch TLE: {e}")


def parse_tle_blocks(tle_text: str) -> List[Tuple[str, str, str]]:
    lines = [ln.strip() for ln in tle_text.splitlines() if ln.strip()]
    out = []
    i = 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        # Basic sanity check
        if l1.startswith("1 ") and l2.startswith("2 "):
            out.append((name, l1, l2))
            i += 3
        else:
            # Try to resync if a malformed header appears
            i += 1
    return out


def perigee_alt_km_from_tle(line1: str, line2: str) -> float:
    ts = load.timescale()
    sat = EarthSatellite(line1, line2, "sat", ts)
    a_er = sat.model.a  # semi-major axis in Earth radii
    e = float(sat.model.ecco)
    a_km = float(a_er) * EARTH_RADIUS_KM
    perigee_km = a_km * (1.0 - e) - EARTH_RADIUS_KM
    return perigee_km


st.markdown("### 1) Fetch TLE")
if source_mode == "Common group":
    st.write(
        f"Fetching **{group}** from CelesTrak (FORMAT=tle). You can refine with the filters below."
    )
    try:
        raw_tle = fetch_tle_text(params=params)
    except RuntimeError as e:
        st.error(str(e))
        st.stop()
else:
    st.write("Fetching from custom URL…")
    try:
        raw_tle = fetch_tle_text(custom_url=custom_url)
    except RuntimeError as e:
        st.error(str(e))
        st.stop()

all_blocks = parse_tle_blocks(raw_tle)
st.success(f"Loaded {len(all_blocks)} TLE entries.")

st.markdown("### 2) Filter LEO by perigee altitude")
progress = st.progress(0, text="Computing perigee altitudes…")

filtered: List[Tuple[str, str, str]] = []
for idx, (nm, l1, l2) in enumerate(all_blocks):
    try:
        p_km = perigee_alt_km_from_tle(l1, l2)
        passes_alt = p_km <= perigee_max_km
        passes_name = (name_filter.lower() in nm.lower()) if name_filter else True
        if passes_alt and passes_name:
            filtered.append((nm, l1, l2))
    except Exception:
        # skip malformed/edge cases silently; optional: log with st.debug when available
        pass
    if len(all_blocks) > 0:
        progress.progress(min(100, int((idx + 1) / len(all_blocks) * 100)))

progress.empty()
st.info(
    f"Filter: perigee ≤ **{perigee_max_km} km**" + (f", name contains '**{name_filter}**'" if name_filter else "")
)
st.success(f"LEO matches: **{len(filtered)}** / {len(all_blocks)}")

if len(filtered) == 0:
    st.warning("No satellites matched the filter. Try increasing the threshold or clearing the name filter.")

st.markdown("### 3) Preview (first 10)")
preview_text = io.StringIO()
for triplet in filtered[:10]:
    preview_text.write("\n".join(triplet) + "\n")
st.code(preview_text.getvalue() or "(no results)", language="text")

st.markdown("### 4) Export")
export_text = io.StringIO()
for name, l1, l2 in filtered:
    export_text.write("\n".join((name, l1, l2)) + "\n")

stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
filename_tle = f"{export_basename}_{stamp}.tle"
filename_txt = f"{export_basename}_{stamp}.txt"

col1, col2 = st.columns(2)
with col1:
    st.download_button(
        label=f"Download TLE ({len(filtered)} entries)",
        data=export_text.getvalue(),
        file_name=filename_tle,
        mime="text/plain",
        use_container_width=True,
    )
with col2:
    st.download_button(
        label=f"Download TXT ({len(filtered)} entries)",
        data=export_text.getvalue(),
        file_name=filename_txt,
        mime="text/plain",
        use_container_width=True,
    )

st.caption(
    "Tip: Streamlit Cloud ready. See the setup notes below for quick deployment."
)

st.divider()
st.markdown(
    """
    ## Deployment notes (Streamlit Community Cloud)

    1. **Create a GitHub repo** and add two files:
       - `app.py` (this script)
       - `requirements.txt` with:

         ```
         streamlit
         requests
         skyfield
         numpy
         ```

    2. Push to GitHub.

    3. Go to **share.streamlit.io** (Streamlit Community Cloud) → **New app** → select your repo/branch → file `app.py`.

    4. Click **Deploy**. You can tweak the app later from the same dashboard.

    ### Optional tweaks
    - Add more groups or a free-text `GROUP` input.
    - Add an **inclination** or **mean-motion** filter for polar/Sun-synchronous subsets.
    - Add a checkbox to **dedupe** by NORAD ID.
    - Cache perigee computations with a hash of TLE text if your groups are large.
    """
)
