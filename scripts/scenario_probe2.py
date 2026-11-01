"""Probe the guard-workaround step: after load balancing and one switch-off, the energy agent is denied
cell_2_1 (too busy) and instead tries to drain it by re-tilting.
    python -m scripts.scenario_probe2
"""

import logging
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.INFO)

from twin.engine import TwinEngine  # noqa: E402


def main():
    eng = TwinEngine()
    eng.set_tick(20)
    eng.add_event("Stadium concert", -74.00607, 40.76665, 350, 0.004, [20])
    eng.apply([{"name": "set_tilt", "cell_id": f"cell_3_{k}", "tilt": 20.0} for k in range(3)] +
              [{"name": "set_tilt", "cell_id": f"cell_2_{k}", "tilt": 8.0} for k in range(3)] +
              [{"name": "cell_off", "cell_id": "cell_2_2"}])
    k = eng.kpis()
    print("after LB + cell_2_2 off:", {c: v for c, v in k.ues_per_cell.items() if v > 60}, "excess", k.excess_ues, "max", k.max_cell_load)
    for tilt in (12.0, 16.0, 20.0):
        sh = eng.shadow([{"name": "set_tilt", "cell_id": "cell_2_1", "tilt": tilt}])
        a = sh["after"]
        print(f"  energy agent downtilts cell_2_1 8 -> {tilt}: cell_2_1 {sh['before']['ues_per_cell']['cell_2_1']} -> "
              f"{a['ues_per_cell'].get('cell_2_1')}, max {sh['before']['max_cell_load']} -> {a['max_cell_load']}, "
              f"excess {sh['before']['excess_ues']} -> {a['excess_ues']}, weak {a['weak_coverage_pct']}, "
              f"hot {[(c, v) for c, v in a['ues_per_cell'].items() if v > 100]}")
    for c, tilt in (("cell_2_0", 2.0), ("cell_2_0", 4.0)):
        sh = eng.shadow([{"name": "set_tilt", "cell_id": c, "tilt": tilt}])
        a = sh["after"]
        print(f"  energy agent uptilts {c} -> {tilt}: cell_2_1 -> {a['ues_per_cell'].get('cell_2_1')}, {c} -> "
              f"{a['ues_per_cell'].get(c)}, max {a['max_cell_load']}, excess {a['excess_ues']}")


if __name__ == "__main__":
    main()
