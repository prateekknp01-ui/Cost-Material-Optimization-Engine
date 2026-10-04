# Cost & Material Optimization Engine – Power Network Planner

Upload a marked-up map of a site and get:

- the **network layout**: where every pole goes (and what type: supply, tee, angle, dead-end, intermediate) and which wire runs between them
- **conductor sizes** per segment (checked for current rating and voltage drop)
- transformer sizing, a full **bill of materials** and the **total cost to establish** the network
- a **material recommendation** (Economy / Balanced / Durable / Premium) that balances durability against your budget
- an **ML cost forecast** from load demand and spatial features, shown next to the bottom-up estimate

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py                 # web app, has a "try the sample map" button
```

Command line:

```bash
python -m planner.cli sample/sample_map.png --width-m 600 --voltage LT --budget 1600000 --out output
```

Outputs: `network_overlay.png`, `network_plan.xlsx` (Summary, BOM, Poles, Segments, Nodes, Scenarios), CSVs, `summary.json`.

## How to mark up your map

Use any drawing tool on a screenshot or plan (Paint, Google Maps screenshot + markup, CAD export):

| Colour | Meaning |
|---|---|
| Blue filled shape | supply point (transformer / substation / tap-off) |
| Red filled dots | loads (houses, pumps, shops, …) |
| Cyan / light-blue areas | water or other obstacles (optional) |
| Dark gray / black areas | buildings or no-go zones (optional) |

Then give the **map scale** (real width of the image in metres, or metres per pixel). Detected points appear in an editable table, so you can fix a missed marker, add one by pixel position, or set a different kW for each load. Obstacle detection is optional; turn it off for photographic maps where dark pixels are not no-go zones.

## How it works

1. **`map_parser`** – OpenCV HSV colour detection finds supply and load markers and an obstacle mask (robust to JPEG compression).
2. **`network`** – a minimum spanning tree grows from the supply point(s) to every load; edges that cross obstacles are penalised so the route goes around them where it can.
3. **`design`** – places poles at most one span apart (40 m LT / 60 m HT by default), nudges poles off obstacles, uses taller poles either side of unavoidable crossings, classifies each pole by structure type, then sizes conductors from downstream demand (ampacity + growth margin) and upsizes until the voltage-drop limit is met. Builds the BOM and costs it with labour, terrain factor, contingency, supervision and tax.
4. **`recommend`** – costs the same route under every material scenario and ranks them by **equivalent annual cost** (capex spread over component life + maintenance), restricted to options inside the budget.
5. **`ml_cost`** – gradient-boosted model that predicts total cost from total kW, number of loads, area, load spacing, distance to supply, voltage level, terrain and material tier.

## Be aware of these limits

- **Catalog rates are indicative placeholders.** Dump and edit them: `python -m planner.catalog --dump my_catalog.json`, then pass `--catalog my_catalog.json`. Use your utility's schedule of rates before quoting any figure.
- **The ML model is trained on simulated projects** costed by this package's own bottom-up engine, so it reproduces the catalog's economics (held-out error is reported in the app). To make it learn real behaviour, retrain on completed projects: `python -m planner.ml_cost --data past_projects.csv` (columns: `total_cost, total_kw, n_loads, area_ha, mean_nn_m, mean_src_dist_m, is_ht, terrain, scenario_idx`).
- Routing is straight-line between points (no road or right-of-way following) and branches only at load points. Treat the layout as a preliminary design for estimating, not a construction drawing.
- Voltage drop and conductor loading use a balanced three-phase, steady-state model. Short-circuit, mechanical (wind/tension) and protection checks are not included.

## Project layout

```
app.py                Streamlit app
planner/              map_parser, network, design, recommend, ml_cost, render, report, catalog, cli
models/               trained cost forecaster
sample/               demo map and example output
tests/                pytest suite  (python -m pytest -q tests)
```
