"""What does the trained PPO agent propose per tick, and what does the framework do with it?

Part 1 (offline): for each tick 0..23, the agent's per-cell output translated to vocabulary actions
against the default network, and the twin KPIs of that configuration vs baseline.
Part 2 (live): the agent's unedited proposal for each tick submitted as the Energy Saving rApp
through the running gateway; decision and policy ID recorded. Needs `python run_demo.py`.

    python -m scripts.analyze_agent [--agent path.zip] [--offline-only]
"""

import argparse
import json
import logging
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.INFO)

import httpx  # noqa: E402
from stable_baselines3 import PPO  # noqa: E402

from framework.cards import load_cell_tags  # noqa: E402
from rapps.client import GatewayClient  # noqa: E402
from rapps.energy_saving import EnergySavingRapp, latest_agent_path  # noqa: E402
from twin.vocabulary import OFF_INDEX, TILT_SET, rl_actions_to_vocabulary  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = "http://127.0.0.1:8700"


def offline(model, ticks):
    from twin.engine import TwinEngine

    eng = TwinEngine()
    tags = load_cell_tags(ROOT / "policies")
    default = {c: {"on": True, "tilt": eng.default_state.tilt[c]} for c in eng.cell_ids}
    rows = []
    for t in ticks:
        idx, _ = model.predict(t, deterministic=True)
        idx = [int(i) for i in idx]
        actions = rl_actions_to_vocabulary(idx, eng.cell_ids, default)
        eng.set_tick(t)
        eng.reset()
        base = eng.kpis(t)
        after = eng.simulate(eng._apply_to(eng.live, actions), t)
        off_cells = [c for c, i in zip(eng.cell_ids, idx) if i == OFF_INDEX]
        rows.append({
            "tick": t, "raw_indices": idx, "n_actions": len(actions),
            "cells_off": off_cells, "n_off": len(off_cells),
            "critical_off": [c for c in off_cells if tags[c] == "critical"],
            "tilts": {c: TILT_SET[i] for c, i in zip(eng.cell_ids, idx) if i != OFF_INDEX},
            "n_tilt_changes": sum(a["name"] == "set_tilt" for a in actions),
            "distinct_cells_changed": len({a["cell_id"] for a in actions}),
            "weak_before": base.weak_coverage_pct, "weak_after": after.weak_coverage_pct,
            "energy_after": after.energy_saving_pct, "actions": actions,
        })
    return rows


def live(ticks, agent_path: Path):
    ops = httpx.Client(base_url=GATEWAY, timeout=300)
    ops.post("/ops/settings", json={"enforcement": True, "screening": True}).raise_for_status()
    ops.post("/ops/reset", json={"registrations": True}).raise_for_status()
    es = EnergySavingRapp(GatewayClient(GATEWAY), agent_path)
    es.register()
    rows = []
    for t in ticks:
        ops.post("/ops/reset", json={"tick": t}).raise_for_status()
        res = es.run_agent()
        o = res.get("outcome") or {}
        if o.get("decision") == "pending_approval":  # leave the queue clean; the reject is recorded
            ops.post(f"/ops/approvals/{o['request_id']}", json={"approve": False, "approver": "analysis", "reason": "analysis run"})
        rows.append({"tick": t, "n_submitted": len(res["submitted"]), "decision": o.get("decision"),
                     "policy_id": o.get("policy_id"), "reason": o.get("reason"), "ledger_seq": o.get("ledger_seq"),
                     "failed_checks": [c["check"] for c in o.get("checks", []) if c.get("result") == "deny"]})
    ops.post("/ops/reset", json={"registrations": True}).raise_for_status()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=None)
    ap.add_argument("--offline-only", action="store_true")
    args = ap.parse_args()
    path = Path(args.agent) if args.agent else latest_agent_path()
    model = PPO.load(str(path), device="cpu")
    ticks = list(range(24))
    off_rows = offline(model, ticks)
    live_rows = None if args.offline_only else live(ticks, path)

    print(f"Agent: {path.name}\n")
    print("tick | OFF | crit OFF | tilt chg | cells chg | weak before -> after | energy | framework")
    for i, r in enumerate(off_rows):
        fw = ""
        if live_rows:
            l = live_rows[i]
            fw = f"{l['decision']} {l['policy_id'] or ''}"
        print(f"{r['tick']:4d} | {r['n_off']:3d} | {len(r['critical_off']):8d} | {r['n_tilt_changes']:8d} | {r['distinct_cells_changed']:9d} | "
              f"{r['weak_before']:6.1f} -> {r['weak_after']:6.1f} | {r['energy_after']:5.1f}% | {fw}")
    summary = {
        "agent": path.name,
        "ticks": len(off_rows),
        "mean_cells_off": round(sum(r["n_off"] for r in off_rows) / len(off_rows), 2),
        "max_cells_off": max(r["n_off"] for r in off_rows),
        "ticks_with_critical_off": sum(1 for r in off_rows if r["critical_off"]),
        "mean_distinct_cells_changed": round(sum(r["distinct_cells_changed"] for r in off_rows) / len(off_rows), 2),
        "distinct_proposals": len({tuple(r["raw_indices"]) for r in off_rows}),
        "mean_weak_before": round(sum(r["weak_before"] for r in off_rows) / len(off_rows), 2),
        "mean_weak_after": round(sum(r["weak_after"] for r in off_rows) / len(off_rows), 2),
        "mean_energy_after": round(sum(r["energy_after"] for r in off_rows) / len(off_rows), 2),
        "cells_most_often_off": Counter(c for r in off_rows for c in r["cells_off"]).most_common(6),
    }
    if live_rows:
        summary["framework_decisions"] = Counter(l["decision"] for l in live_rows)
        summary["framework_policy_ids"] = Counter(l["policy_id"] for l in live_rows)
    print("\n" + json.dumps(summary, indent=2))
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    (out / f"agent_analysis_{stamp}.json").write_text(json.dumps(
        {"summary": summary, "offline": off_rows, "framework": live_rows}, indent=2, default=str), encoding="utf-8")
    print(f"\nReport: reports/agent_analysis_{stamp}.json")


if __name__ == "__main__":
    main()
