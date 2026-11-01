"""Phase-1 gate: shadow-run time (SH-2) and which single cell-OFF gives a visible KPI change."""
import logging, statistics, sys, time, warnings
warnings.filterwarnings("ignore")
logging.disable(logging.INFO)
from twin.engine import TwinEngine

tick = int(sys.argv[1]) if len(sys.argv) > 1 else 3
t0 = time.time(); eng = TwinEngine(); eng.set_tick(tick)
print(f"load {time.time()-t0:.1f}s")
base = eng.kpis(); print("baseline", base.as_dict())
times = []
for c in eng.cell_ids:
    r = eng.shadow([{"name": "cell_off", "cell_id": c}]); times.append(r["seconds"])
    print(f"{c:9s} OFF  weak {r['before']['weak_coverage_pct']:6.2f} -> {r['after']['weak_coverage_pct']:6.2f} (d {r['delta']['weak_coverage_pct']:+6.2f})  over d {r['delta']['over_coverage_pct']:+6.2f}  {r['seconds']:.2f}s")
batch = [{"name": "cell_off", "cell_id": c} for c in ("cell_2_0", "cell_2_1", "cell_3_2")]
r = eng.shadow(batch); print("batch 3 OFF", r["delta"], r["seconds"])
r = eng.shadow([{"name": "set_tilt", "cell_id": "cell_1_0", "tilt": 2.0}]); print("tilt", r["delta"], r["seconds"])
print(f"shadow median {statistics.median(times):.2f}s  max {max(times):.2f}s  (SH-2 target <= 10s)")
