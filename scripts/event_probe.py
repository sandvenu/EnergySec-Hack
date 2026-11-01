"""Feasibility probe: does the MAVERIC twin show the load-balancing vs energy-saving conflict?

A stadium crowd is added near one site; a load-balancing agent re-tilts to spread users; an energy
agent then switches off a neighbour that looks lightly loaded. We print users per cell, the most
loaded cell and weak coverage at each step.
    python -m scripts.event_probe
"""

import logging
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.INFO)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from twin.engine import TwinEngine  # noqa: E402

TICK = 20  # 20:00, evening event


def with_event(eng, tick, center, n, sigma_deg, seed=1):
    base = eng.ue_per_tick[tick]
    rng = np.random.default_rng(seed)
    lon = rng.normal(center[0], sigma_deg, n)
    lat = rng.normal(center[1], sigma_deg * 0.75, n)
    crowd = pd.DataFrame({"mock_ue_id": np.arange(10_000, 10_000 + n), "loc_x": lon, "loc_y": lat, "tick": tick})
    eng.ue_per_tick[tick] = pd.concat([base, crowd[base.columns.intersection(crowd.columns)]], ignore_index=True)


def show(label, k):
    load = k.ues_per_cell
    top = sorted(load.items(), key=lambda x: -x[1])[:5]
    print(f"{label:38s} weak {k.weak_coverage_pct:5.1f}%  active {k.active_cells:2d}  max-load {max(load.values()):4d}  "
          f"top {top}")


def main():
    eng = TwinEngine()
    sites = eng.site_config.groupby("site_id")[["cell_lon", "cell_lat"]].first()
    print(sites)
    s3 = sites.loc["Site3"]
    eng.set_tick(TICK)
    show("baseline (no event)", eng.kpis())
    # stadium a little north-east of Site3, towards Site5
    center = (s3.cell_lon + 0.004, s3.cell_lat + 0.002)
    with_event(eng, TICK, center, 350, 0.0025)
    k0 = eng.kpis()
    show("stadium crowd +350 UEs", k0)

    s = eng.live.copy()
    hot = max(k0.ues_per_cell, key=k0.ues_per_cell.get)
    print("hottest cell:", hot)
    for tilt in (16.0, 20.0):  # load balancing: downtilt the hot cell to shrink its footprint
        t = s.copy(); t.tilt[hot] = tilt
        show(f"LB: downtilt {hot} -> {tilt}", eng.simulate(t))
    for c in [c for c in eng.cell_ids if c != hot]:  # uptilt each neighbour candidate
        t = s.copy(); t.tilt[hot] = 20.0; t.tilt[c] = 4.0
        k = eng.simulate(t)
        print(f"   + uptilt {c} -> 4: load hot={k.ues_per_cell.get(hot)} {c}={k.ues_per_cell.get(c)} max={max(k.ues_per_cell.values())} weak {k.weak_coverage_pct}")


if __name__ == "__main__":
    main()
