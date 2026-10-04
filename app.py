"""Streamlit app: upload a marked-up site map, get a pole-and-wire layout and a cost."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pandas as pd
import streamlit as st

from planner.catalog import load_catalog
from planner.config import TERRAIN_FACTORS, PlanConfig
from planner.map_parser import decode_image, parse_map
from planner.ml_cost import CostForecaster
from planner.network import build_topology
from planner.recommend import compare_scenarios
from planner.render import render_plan
from planner.report import export_excel
from planner.sample_map import make_sample_map

ROOT = Path(__file__).parent
SAMPLE = ROOT / "sample" / "sample_map.png"

st.set_page_config(page_title="Power Network Planner", page_icon="⚡", layout="wide")


def _use_sample() -> None:
    st.session_state["use_sample"] = True
    st.session_state["detect_obs"] = True
    st.session_state["width_m"] = 600.0


st.session_state.setdefault("detect_obs", False)
st.session_state.setdefault("width_m", 600.0)


@st.cache_resource(show_spinner="Loading cost forecaster...")
def get_forecaster() -> CostForecaster:
    return CostForecaster.load_or_train()


def inr(x: float) -> str:
    return f"₹ {x:,.0f}"


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Site settings")
    scale_mode = st.radio("Map scale given as", ["Real width of the map (m)", "Metres per pixel"])
    if scale_mode.startswith("Real"):
        st.number_input("Real width of the whole map (m)", min_value=10.0, max_value=100000.0,
                        step=10.0, key="width_m",
                        help="Use the scale bar of your map to estimate the left-to-right distance.")
        mpp_in = None
    else:
        mpp_in = st.number_input("Metres per pixel", min_value=0.01, max_value=1000.0, value=0.5, step=0.05)
    voltage = st.selectbox("Network voltage", ["LT (415 V)", "HT (11 kV)"])
    default_kw = st.number_input("Load per red marker (kW)", min_value=0.1, value=5.0, step=0.5)
    terrain_label = st.selectbox("Terrain / site conditions", list(TERRAIN_FACTORS))
    budget_in = st.number_input("Budget in ₹ (0 = no limit)", min_value=0.0, value=0.0, step=100000.0)
    st.checkbox("Detect obstacles (cyan = water, dark gray = no-go)", key="detect_obs")
    with st.expander("Advanced"):
        diversity = st.slider("Diversity factor", 0.3, 1.0, 0.8, 0.05)
        pf = st.slider("Power factor", 0.7, 1.0, 0.9, 0.01)
        vd_limit = st.slider("Voltage-drop limit (%)", 2.0, 10.0, 5.0, 0.5)
        span_in = st.number_input("Max span (m, 0 = default)", min_value=0.0, value=0.0, step=5.0)
        cont = st.number_input("Contingency (%)", 0.0, 50.0, 10.0, 1.0)
        sup = st.number_input("Supervision (%)", 0.0, 50.0, 5.0, 1.0)
        tax = st.number_input("Tax / GST (%)", 0.0, 50.0, 0.0, 1.0)
    st.caption("Unit rates in the catalog are indicative placeholders. Replace them with your "
               "utility's schedule of rates for a bankable estimate.")

# --------------------------------------------------------------------- main
st.title("⚡ Power Network Planner")
st.caption("Upload a marked-up site map → get pole and wire positions, conductor sizes, a bill of "
           "materials and the total cost to establish the network.")

up = st.file_uploader("Upload your site map (PNG or JPG)", type=["png", "jpg", "jpeg"])
st.button("Or try the sample map", on_click=_use_sample)

img_bytes = None
if up is not None:
    img_bytes = up.getvalue()
elif st.session_state.get("use_sample"):
    if not SAMPLE.exists():
        make_sample_map(SAMPLE)
    img_bytes = SAMPLE.read_bytes()

if img_bytes is None:
    st.info("**How to mark up your map** (any drawing tool works - Paint, Google Maps screenshot + markup, etc.)")
    st.markdown(
        "- 🟦 **Blue filled shape** - the supply point (transformer / substation / tap-off)\n"
        "- 🔴 **Red filled dots** - each load (house, pump, shop, ...)\n"
        "- 🟦 *Cyan / light-blue areas* - water or other obstacles (optional)\n"
        "- ⬛ *Dark gray / black areas* - buildings or no-go zones (optional)\n\n"
        "Then set the map scale in the sidebar. Detected points can be edited in a table before planning."
    )
    st.stop()

try:
    img = decode_image(img_bytes)
except ValueError as e:
    st.error(str(e))
    st.stop()

mpp = (st.session_state["width_m"] / img.shape[1]) if mpp_in is None else mpp_in
vl = "LT" if voltage.startswith("LT") else "HT"
detect_obs = bool(st.session_state["detect_obs"])
parsed = parse_map(img, default_kw, detect_obstacles=detect_obs)

st.subheader("1 · Detected points")
sig = hashlib.md5(img_bytes).hexdigest()[:8]
pts_df = pd.DataFrame(parsed.points, columns=["kind", "x_px", "y_px", "load_kw"]).round(1)
edited = st.data_editor(
    pts_df, key=f"pts_{sig}_{default_kw}_{detect_obs}", num_rows="dynamic", width="stretch",
    column_config={
        "kind": st.column_config.SelectboxColumn("Type", options=["source", "load"], required=True),
        "x_px": st.column_config.NumberColumn("x (px)"),
        "y_px": st.column_config.NumberColumn("y (px)"),
        "load_kw": st.column_config.NumberColumn("Load (kW)", min_value=0.0),
    },
)
points = [r for r in edited.dropna(subset=["kind", "x_px", "y_px"]).to_dict("records")]
for p in points:
    if pd.isna(p.get("load_kw")):
        p["load_kw"] = default_kw if p["kind"] == "load" else 0.0

cfg = PlanConfig(
    voltage_level=vl, meters_per_pixel=mpp, default_load_kw=default_kw, diversity_factor=diversity,
    power_factor=pf, voltage_drop_limit_pct=vd_limit, max_span_m=span_in or None,
    terrain_factor=TERRAIN_FACTORS[terrain_label], contingency_pct=cont, supervision_pct=sup,
    tax_pct=tax, budget=budget_in or None,
)

try:
    topo = build_topology(points, parsed.obstacle_mask, cfg)
except ValueError as e:
    st.warning(str(e))
    st.image(img, channels="BGR", width="stretch")
    st.stop()

cat = load_catalog()
results, table, rec = compare_scenarios(topo, cfg, cat)

st.subheader("2 · Material strategy")
(st.success if rec["fits_budget"] else st.warning)(f"**Recommended: {rec['name']}.** {rec['reason']}")
names = list(results)
choice = st.radio("Show plan for", names, index=names.index(rec["name"]), horizontal=True)
res = results[choice]
s = res.summary

st.subheader("3 · Network plan")
c = st.columns(5)
c[0].metric("Total establishment cost", inr(s["grand_total"]))
c[1].metric("Poles", s["n_poles"])
c[2].metric("Line length", f"{s['route_length_m']:,.0f} m")
c[3].metric("Max voltage drop", f"{s['max_voltage_drop_pct']:.1f} %")
c[4].metric("Equivalent annual cost", inr(s["annualized_cost"]) if s["annualized_cost"] else "-")
if s["within_budget"] is False:
    st.error(f"Over budget by {inr(s['grand_total'] - s['budget'])}.")
for w in res.warnings:
    st.warning(w)

fig = render_plan(img, res)
st.pyplot(fig, width="stretch")

tabs = st.tabs(["Bill of materials", "Poles", "Segments & wires", "Scenario comparison", "ML cost forecast"])
with tabs[0]:
    st.dataframe(res.bom, width="stretch", hide_index=True)
    brk = pd.DataFrame([
        ("Materials", s["materials_total"]), ("Labour", s["labour_total"]),
        (f"Contingency ({cont:.0f}%)", s["contingency"]), (f"Supervision ({sup:.0f}%)", s["supervision"]),
        (f"Tax ({tax:.0f}%)", s["tax"]), ("TOTAL", s["grand_total"]),
    ], columns=["Component", "Amount (₹)"])
    st.dataframe(brk, width="stretch", hide_index=True)
    if s["transformers"]:
        st.caption("Transformers: " + "; ".join(
            f"{t['units']} × {t['rating_kva']} kVA at {t['location']}" for t in s["transformers"]))
with tabs[1]:
    st.dataframe(res.poles, width="stretch", hide_index=True)
with tabs[2]:
    st.dataframe(res.segments, width="stretch", hide_index=True)
with tabs[3]:
    st.dataframe(table, width="stretch", hide_index=True)
with tabs[4]:
    fc = get_forecaster()
    pred = fc.predict(topo, cfg, choice)
    a, b = st.columns(2)
    a.metric("ML forecast (before detailed design)", inr(pred["cost"]),
             help=f"Typical error on held-out data: ±{pred['typical_error_pct']:.1f}%")
    b.metric("Bottom-up bill of materials", inr(s["grand_total"]),
             delta=f"{(pred['cost'] / s['grand_total'] - 1) * 100:+.1f}% forecast vs BOM", delta_color="off")
    st.caption(
        f"Forecast range {inr(pred['low'])} - {inr(pred['high'])}. The forecaster uses only load demand and "
        f"spatial features (total kW, number of loads, area, spacing, distance to supply). It was trained on "
        f"**{pred['data_source']}** projects, so it reproduces the catalog's economics; retrain it on your own "
        "completed projects with `python -m planner.ml_cost --data your_projects.csv`."
    )

st.subheader("4 · Download")
buf = io.BytesIO()
export_excel(res, buf, table)
png = io.BytesIO()
fig.savefig(png, format="png", dpi=150, bbox_inches="tight")
d1, d2 = st.columns(2)
d1.download_button("⬇ Excel (summary, BOM, poles, segments)", buf.getvalue(),
                   file_name="network_plan.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
d2.download_button("⬇ Network overlay (PNG)", png.getvalue(), file_name="network_overlay.png", mime="image/png")
