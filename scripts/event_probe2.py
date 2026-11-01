"""Probe 2: crowd between two sites, so tilts can split it; then an energy switch-off on top.
    python -m scripts.event_probe2
"""

import itertools
import logging
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.INFO)

from scripts.event_probe import TICK, with_event  # noqa: E402
from twin.engine import TwinEngine  # noqa: E402


def site_load(k, site):
    return sum(v for c, v in k.ues_per_cell.items() if c.startswith(f"cell_{site}_"))


def main():
    eng = TwinEngine()
    sites = eng.site_config.groupby("site_id")[["cell_lon", "cell_lat"]].first()
    a, b = sites.loc["Site3"], sites.loc["Site2"]
    for frac in (0.35, 0.5):
        e2 = TwinEngine()
        e2.set_tick(TICK)
        center = (a.cell_lon + (b.cell_lon - a.cell_lon) * frac, a.cell_lat + (b.cell_lat - a.cell_lat) * frac)
        with_event(e2, TICK, center, 350, 0.004)
        k0 = e2.kpis()
        print(f"\n=== crowd {frac:.2f} of the way Site3 -> Site2: max {max(k0.ues_per_cell.values())} "
              f"site3 {site_load(k0, 3)} site2 {site_load(k0, 2)} weak {k0.weak_coverage_pct}")
        print("   loads:", {c: v for c, v in sorted(k0.ues_per_cell.items()) if v > 30})
        best = []
        for t3, t2 in itertools.product((8.0, 12.0, 16.0, 20.0), (2.0, 4.0, 8.0, 12.0)):
            s = e2.live.copy()
            for k in range(3):
                s.tilt[f"cell_3_{k}"] = t3
                s.tilt[f"cell_2_{k}"] = t2
            kk = e2.simulate(s)
            best.append((max(kk.ues_per_cell.values()), t3, t2, kk.weak_coverage_pct, site_load(kk, 3), site_load(kk, 2),
                         {c: v for c, v in kk.ues_per_cell.items() if v > 80}))
        for row in sorted(best)[:5]:
            print("   tilt3=%-4s tilt2=%-4s max %4d weak %5.1f site3 %d site2 %d hot %s" % (row[1], row[2], row[0], row[3], row[4], row[5], row[6]))
        # energy agent on top of the best LB config: switch off each site-2 cell in turn
        _, t3, t2, *_ = sorted(best)[0]
        s = e2.live.copy()
        for k in range(3):
            s.tilt[f"cell_3_{k}"] = t3
            s.tilt[f"cell_2_{k}"] = t2
        base = e2.simulate(s)
        for k in range(3):
            o = s.copy(); o.on[f"cell_2_{k}"] = False
            kk = e2.simulate(o)
            print(f"   + energy OFF cell_2_{k}: max {max(base.ues_per_cell.values())} -> {max(kk.ues_per_cell.values())}, "
                  f"weak {base.weak_coverage_pct} -> {kk.weak_coverage_pct}, hot {[(c, v) for c, v in kk.ues_per_cell.items() if v > 80]}")
        o = s.copy()
        for k in range(3):
            o.on[f"cell_2_{k}"] = False
        kk = e2.simulate(o)
        print(f"   + energy OFF all site 2: max -> {max(kk.ues_per_cell.values())}, weak -> {kk.weak_coverage_pct}")


if __name__ == "__main__":
    main()
