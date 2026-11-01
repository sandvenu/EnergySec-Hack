import logging, warnings; warnings.filterwarnings("ignore"); logging.disable(logging.INFO)
import numpy as np
from twin.engine import TwinEngine
eng = TwinEngine()
for tick in (3, 14):
    eng.set_tick(tick)
    _, p0 = eng.simulate(eng.live, with_points=True); r0 = np.array([p["rsrp_dbm"] for p in p0])
    print(f"tick {tick} baseline mean {r0.mean():.2f}")
    for site in range(1, 6):
        s = eng.live.copy()
        for k in range(3): s.on[f"cell_{site}_{k}"] = False
        _, p = eng.simulate(s, with_points=True); r = np.array([q["rsrp_dbm"] for q in p])
        print(f"  site {site} OFF  mean d {r.mean()-r0.mean():+6.2f} dB | weak@-130 {100*(r0<=-130).mean():5.1f}% -> {100*(r<=-130).mean():5.1f}% | weak@-135 {100*(r0<=-135).mean():5.1f}% -> {100*(r<=-135).mean():5.1f}%")
