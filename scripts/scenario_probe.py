"""Tune the demo scenario on the real twin: per-cell loads at each step of the planned-event story
and of the night-time guard workaround. Prints the numbers the thresholds are chosen from.
    python -m scripts.scenario_probe
"""

import logging
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.INFO)

from twin.engine import TwinEngine  # noqa: E402

EVENT_TICK = 20


def show(label, k, cells=None):
    hot = {c: v for c, v in sorted(k.ues_per_cell.items()) if (cells and c in cells) or v > 100}
    print(f"{label:44s} weak {k.weak_coverage_pct:5.1f}%  max {k.max_cell_load:4d}  overloaded {k.overloaded_cells}  {hot}")


def main():
    eng = TwinEngine()
    sites = eng.site_config.groupby("site_id")[["cell_lon", "cell_lat"]].first()
    a, b = sites.loc["Site3"], sites.loc["Site2"]
    lon, lat = a.cell_lon + (b.cell_lon - a.cell_lon) * 0.35, a.cell_lat + (b.cell_lat - a.cell_lat) * 0.35
    print(f"stadium at lon {lon:.5f} lat {lat:.5f}")
    site2 = ["cell_2_0", "cell_2_1", "cell_2_2"]

    eng.set_tick(EVENT_TICK)
    show("20:00, no event", eng.kpis(), site2)
    eng.add_event("Stadium concert", lon, lat, 350, 0.004, [EVENT_TICK])
    show("20:00, concert (+350 users)", eng.kpis(), site2)
    lb = [{"name": "set_tilt", "cell_id": f"cell_3_{k}", "tilt": 20.0} for k in range(3)] + \
         [{"name": "set_tilt", "cell_id": f"cell_2_{k}", "tilt": 8.0} for k in range(3)]
    eng.apply(lb)
    show("load-balancing tilts applied", eng.kpis(), site2)
    for c in ("cell_2_2", "cell_2_0", "cell_2_1"):
        sh = eng.shadow([{"name": "cell_off", "cell_id": c}])
        print(f"   shadow OFF {c}: max {sh['before']['max_cell_load']} -> {sh['after']['max_cell_load']}, "
              f"weak {sh['before']['weak_coverage_pct']} -> {sh['after']['weak_coverage_pct']}, "
              f"its load before {sh['before']['ues_per_cell'].get(c)}")
        eng.apply([{"name": "cell_off", "cell_id": c}])
        show(f"   energy OFF {c}", eng.kpis(), site2)

    print("\n--- night workaround (no event) ---")
    eng.clear_event()
    eng.reset()
    eng.set_tick(2)
    k = eng.kpis()
    low = ["cell_2_0", "cell_2_1", "cell_2_2", "cell_3_0", "cell_3_1", "cell_3_2"]
    print("02:00 loads on low-criticality cells:", {c: k.ues_per_cell[c] for c in low})
    for c in low:
        sh = eng.shadow([{"name": "set_tilt", "cell_id": c, "tilt": 20.0}])
        print(f"   downtilt {c} -> 20: its load {sh['before']['ues_per_cell'][c]} -> {sh['after']['ues_per_cell'].get(c)}, "
              f"max {sh['before']['max_cell_load']} -> {sh['after']['max_cell_load']}")


if __name__ == "__main__":
    main()
